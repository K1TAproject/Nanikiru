"""Record -> visible prefix -> standard request -> dispatch -> unified output."""

from nanikiru import RoundRecord, UNKNOWN
from nanikiru.processing import Session
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.analysis import analyze
from nanikiru.analysis.output import summarize_result
from .inspect_position import sample_position


if __name__ == "__main__":
    session = Session(RoundRecord(sample_position()))
    session.append("discard", actor=0,
                   data={"tile_id": "t13", "code": "9m", "discard_kind": UNKNOWN})
    for through in (0, 1):
        request = build_analysis_request(session.record, source_version=session.state_version, through=through)
        result = analyze(request)
        print(f"\nSelected step {through}; input contains {len(request.history)} events")
        print(summarize_result(request, result, "t13"))
