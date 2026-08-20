from vision import parse_json_response


def test_parse_fenced_json():
    result = parse_json_response(
        '```json\n{"closed_tiles":"123m456p789s11122z","confidence":0.95}\n```'
    )
    assert result["closed_tiles"] == "123m456p789s11122z"
    assert result["confidence"] == 0.95

