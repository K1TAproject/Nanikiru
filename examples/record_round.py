"""Run from project root: python -m examples.record_round"""

from pathlib import Path

from nanikiru import (
    Discard, DiscardRecord, Draw, DrawnTile, HandState, Meld, MeldKind,
    Phase, PlayerState, Recorder, RoundState, Tile,
)


def tile_set():
    tiles = []
    for suit in "mpsz":
        for rank in range(1, 8 if suit == "z" else 10):
            tiles.extend(Tile(suit, rank, i == 0 and rank == 5 and suit != "z") for i in range(4))
    return tiles


def sample_snapshot(meld_kind=None):
    """Synthetic stable snapshot, not a real log or a winning-hand example.

    Fixture-only stock allocates distinct physical copies to prevent contradictory
    inputs. The recorder never receives the stock or opponents' tile identities.
    """
    stock = tile_set()

    def take(code):
        tile = Tile.parse(code)
        stock.remove(tile)
        return tile

    own = [take(f"{i}s") for i in range(1, 10)] + [take(f"{i}z") for i in range(1, 5)]
    drawn = take("5z")
    indicators = [take("6z")]
    players = [PlayerState(i, 25000, HandState(unknown_count=13)) for i in range(4)]
    players[0].hand = HandState(own, drawn_tile=DrawnTile(drawn))
    claimed = None
    if meld_kind is not None:
        codes = (["1m", "2m", "3m"] if meld_kind == MeldKind.CHI
                 else ["1m"] * (3 if meld_kind == MeldKind.PON else 4))
        tiles = [take(code) for code in codes]
        source = None if meld_kind == MeldKind.CLOSED_KAN else "snapshot:0:0"
        players[1].melds = [Meld(meld_kind, tiles, source)]
        players[1].hand.unknown_count = 10
        if source is not None:
            claimed = DiscardRecord(source, tiles[0], claimed_by=1)
        if len(tiles) == 4:
            indicators.append(take("7z"))
    for p in players:
        for j in range(16):
            if p.seat == 0 and j == 0 and claimed is not None:
                p.discards.append(claimed)
            else:
                p.discards.append(DiscardRecord(f"snapshot:{p.seat}:{j}", stock.pop(0)))
    state = RoundState(players, observer_seat=0, dealer_seat=0, actor=0,
                       dora_indicators=indicators)
    return state, stock[:state.remaining_draws]


def finish(recorder, future_tiles):
    for tile in future_tiles:
        seat = (recorder.state.actor + 1) % 4
        recorder.submit(Draw(seat, tile if seat == recorder.state.observer_seat else None))
        recorder.submit(Discard(seat, tile, True))


def main():
    initial, future = sample_snapshot()
    recorder = Recorder(initial)
    recorder.submit(Discard(0, initial.players[0].hand.drawn_tile.tile, True))
    recorder.stop()
    output_dir = Path(__file__).resolve().parent
    paused_file = output_dir / "paused_round.json"
    recorder.save(paused_file)
    recorder = Recorder.load(paused_file)
    recorder.resume()
    finish(recorder, future)
    assert recorder.state.phase == Phase.EXHAUSTED
    final_file = output_dir / "completed_round.json"
    recorder.save(final_file)
    loaded = Recorder.load(final_file)
    assert loaded.state == recorder.state == recorder.replay()
    loaded.undo()
    assert loaded.state.phase == Phase.DISCARD and loaded.state.remaining_draws == 0
    print(f"Verified {len(recorder.events)} events; remaining draws: {recorder.state.remaining_draws}")
    print(f"Saved {paused_file} and {final_file}")


if __name__ == "__main__":
    main()
