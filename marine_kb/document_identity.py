"""Conservative textual identities for explicitly requested notices."""
import re
import unicodedata


def notice_codes(text):
    """Return issuer abbreviation, year and issue, keeping all three distinct.

    These are retrieval identities, not proof that a document is in force.
    Do not infer an issuer or substitute a notice that merely cites the code.
    """
    compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text)))
    found = []
    pattern = r'([\u4e00-\u9fff]{2,16})[〔\[【(]((?:19|20)\d{2})[〕\]】)](\d{1,4})号'
    for match in re.finditer(pattern, compact):
        issuer = re.sub(r'^(?:(?:请问|根据|按照|依据|引用|查询|按|由|原|现行|有关))+', '', match[1])
        if len(issuer) >= 2:
            code = (issuer, match[2], str(int(match[3])))
            if code not in found:
                found.append(code)
    return found
