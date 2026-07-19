

def test_member_import_allows_a_trailing_comma(ts):
    """
    A multi-line member list may close with a trailing comma.
    """
    from siec.parser.functions import parse_import

    imp = parse_import(ts("""
        import {
            a,
            b as c,
        } from mod.sub;
    """))

    assert imp.path == "mod.sub"
    assert imp.members == [("a", "a"), ("b", "c")]
