"""Single-hand payments and next-hand metadata. No strategy or UI imports."""

from ..tiles import index
from ..rules.scoring import WINDS


def _pay(game, payments, payer, winner, amount, reason):
    if amount and payer != winner:
        game._scores[payer] -= amount
        game._scores[winner] += amount
        payments.append(dict(payer=payer, winner=winner, amount=amount, reason=reason))


def _end(game, kind, payments, *, repeat, winners=(), details=None):
    old = game._initial["scores"]
    match = None
    if game.rules.tenhou_match_end:
        from ..rules.match import match_status
        match = match_status(game._scores, dealer=game.dealer, wind=game.round_wind,
                             number=game.hand_number, repeat=repeat, kind=kind,
                             match_length=game.rules.match_length, bankruptcy=game.rules.bankruptcy)
        if match['ended']:
            match['final_deposits'] = game._sticks * 1000
            game._scores[match['ranking'][0]] += game._sticks * 1000
            game._sticks = 0
            match['result_points'] = [(game._scores[s]-30000)/1000 + (20,10,-10,-20)[i]
                                      + (20 if i == 0 else 0) for i,s in enumerate(match['ranking'])]
    number = game.hand_number if repeat else game.hand_number % 4 + 1
    wind = game.round_wind if repeat or game.hand_number != 4 else WINDS[(WINDS.index(game.round_wind)+1) % 4]
    game._result = dict(kind=kind, winners=list(winners), payments=payments, details=details or {},
                        initial_scores=old[:], scores=game._scores[:],
                        score_delta=[n-o for n, o in zip(game._scores, old)],
                        riichi_sticks=game._sticks, dealer_repeats=repeat,
                        bankruptcy=game.rules.bankruptcy and min(game._scores) < 0,
                        next_round=dict(dealer=game.dealer if repeat else (game.dealer+1) % 4,
                                        round_wind=wind, hand_number=number,
                                        honba=game.honba+1 if repeat or kind != "win" else 0,
                                        sticks=game._sticks, scores=game._scores[:]))
    game._phase, game._actor, game._pending, game._draw = "ended", None, None, None
    if match is not None:
        game._result['match'] = match
    game._emit("ended", result=game._result)


def _responsibility(game, seat, score):
    if not game.rules.pao:
        return []
    result = []
    for name, kinds, count in (("Daisangen", {31, 32, 33}, 3), ("Dai Suushii", {27, 28, 29, 30}, 4)):
        yaku = next((y for y in score["yaku"] if y["name"] == name and y["yakuman"]), None)
        if not yaku:
            continue
        visible = [g for g in game._groups.values() if g["seat"] == seat
                   and index(game._codes[g["tiles"][0]]) in kinds and g["kind"] != "chi"]
        if len(visible) == count and visible[-1]["source"] is not None:
            result.append((visible[-1]["source"], yaku["han"] // 13))
    return result


def finish_win(game, winners, source, tile, *, rob=None):
    payments, hands = [], []
    for winner in winners:
        score = game._score(winner, tile, tsumo=source is None, rob=rob, reveal_ura=True)
        if score is None:
            raise ValueError("Cannot settle an illegal win")
        cost = score["cost"]
        responsibilities = _responsibility(game, winner, score)
        if responsibilities and game.rules.pao_all_yakuman:
            responsibilities = [(responsibilities[0][0], sum(y['han']//13 for y in score['yaku'] if y['yakuman']))]
        # Responsibility applies only to the responsible yakuman component.
        units = sum(n for _, n in responsibilities)
        if source is not None:
            main_unit = 48000 if winner == game.dealer else 32000
            _pay(game, payments, source, winner, cost["main"]-main_unit*units, "ron")
        else:
            for payer in range(4):
                if payer != winner:
                    unit = 16000 if winner == game.dealer or payer == game.dealer else 8000
                    value = cost["main"] if payer == game.dealer or winner == game.dealer else cost["additional"]
                    _pay(game, payments, payer, winner, value-unit*units, "tsumo")
        for responsible, multiplier in responsibilities:
            total = (48000 if winner == game.dealer else 32000)*multiplier
            if source is None or source == responsible:
                _pay(game, payments, responsible, winner, total, "pao")
            else:
                _pay(game, payments, responsible, winner, total//2, "pao")
                _pay(game, payments, source, winner, total//2, "pao_ron")
        honba = game.honba if not game.rules.honba_head_only or winner == winners[0] else 0
        if responsibilities:
            _pay(game, payments, responsibilities[0][0], winner, 300*honba, "honba")
        elif source is None:
            for payer in range(4):
                if payer != winner:
                    _pay(game, payments, payer, winner, 100*honba, "honba")
        else:
            _pay(game, payments, source, winner, 300*honba, "honba")
        ura_count = game._indicator_count if game.rules.kan_ura else 1
        hands.append(dict(seat=winner, score=score, concealed=[game._codes[t] for t in game._hands[winner]],
                          win_tile=game._codes[tile],
                          ura=[game._codes[game._dead[5+2*i]] for i in range(ura_count)]
                          if game._riichi(winner) and game.rules.ura_dora else []))
    deposits = game._sticks*1000
    game._scores[winners[0]] += deposits
    game._sticks = 0
    _end(game, "win", payments, repeat=game.dealer in winners, winners=winners,
         details=dict(hands=hands, source=source, deposit_winner=winners[0], deposits=deposits, rob=rob))


def finish_draw(game, reason):
    payments, tenpai, nagashi = [], [], []
    if reason == "exhaustive":
        tenpai = [s for s in range(4) if game._waiting(s)]
        if game.rules.nagashi_mangan:
            nagashi = [s for s, river in enumerate(game._rivers) if river and all(
                not r["called"] and (index(game._codes[r["tile"]]) >= 27 or index(game._codes[r["tile"]]) % 9 in (0, 8))
                for r in river)]
        if nagashi:
            for winner in nagashi:
                for payer in range(4):
                    if payer != winner:
                        _pay(game, payments, payer, winner, 4000 if winner == game.dealer or payer == game.dealer else 2000,
                             "nagashi_mangan")
        elif 0 < len(tenpai) < 4:
            # Pairwise transfers preserve the standard 3000-point total.
            for payer in set(range(4))-set(tenpai):
                for winner in tenpai:
                    _pay(game, payments, payer, winner, 3000//(len(tenpai)*(4-len(tenpai))), "noten")
    _end(game, reason, payments, repeat=reason != "exhaustive" or game.dealer in tenpai,
         winners=nagashi, details=dict(tenpai=tenpai, nagashi=nagashi))
