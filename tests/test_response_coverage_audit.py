import unittest
from examples.audit_response_coverage import classify_window


class ResponseCoverageTests(unittest.TestCase):
    def test_uncontested_draw_is_only_a_meld_negative(self):
        self.assertEqual(classify_window(2,{'pon'},{'type':'tsumo','actor':1},0),'inferred_no_meld')
        self.assertEqual(classify_window(2,set(),{'type':'tsumo','actor':1},0),'no_confirmed_meld_opportunity')

    def test_competition_and_terminal_never_fabricate_pass(self):
        self.assertEqual(classify_window(1,{'chi'},{'type':'pon','actor':2},0),'priority_censored')
        self.assertEqual(classify_window(2,{'pon'},{'type':'pon','actor':2},0),'observed_meld')
        for event in ({'type':'hora','actor':3},{'type':'ryukyoku'},{'type':'tsumo','actor':2}):
            self.assertEqual(classify_window(1,{'chi'},event,0),'terminal_or_unresolved')
