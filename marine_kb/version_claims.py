"""Do not turn an archived edition or replacement notice into current validity."""
import re

NORMATIVE=re.compile(r'标准|规范|预案|规程|GB\s*/?\s*T|HY\s*/?\s*T|DB\d{2}')
ASSERT_CURRENT=re.compile(
 r'(?:为|是|属于|仍然?|确认)[^。；！？\n]{0,12}现行(?:有效|版本|标准|规范|预案|规程)'
 r'|现行(?:有效)?(?:版本|标准|规范|预案|规程)(?:为|是|包括|有|清单)'
 r'|(?:按|按照|依据|根据|依照).{0,2}现行(?:的)?(?:标准|规范|预案|规程)'
 r'|现行\s*(?:GB|HY|DB)\s*[/\d]')
NEGATED=re.compile(r'不能|无法|未能|尚未|未核实|待核实|未确认|不确定|不一定|不代表|不等于|是否|能否|是不是|不是|不可')


def verified_current(evidence):
 meta=evidence.get('metadata') or {};app=meta.get('applicability') or {}
 if app.get('temporal') in {'after_cutoff','expired','not_yet_effective'}:return False
 return meta.get('edition_current_verified') is True


def validate_current_version_claims(answer,evidence):
 """Require explicit version-status verification for positive current claims.

 A publication year, an older edition's replacement, a source being official,
 or the model's entailment verdict alone is not that verification. This check
 deliberately permits version-scoped explanations and statements of uncertainty.
 """
 if not NORMATIVE.search(answer):return
 for line in answer.splitlines():
  if not NORMATIVE.search(line):continue
  refs={int(n) for n in re.findall(r'\[资料(\d+)\]',line)}
  for match in ASSERT_CURRENT.finditer(line):
   start=max(line.rfind(mark,0,match.start()) for mark in '，,；;。！？\n：:')+1
   # ASSERT_CURRENT may start at 是 inside 不是. Include the matched
   # span to recognise that negation without accepting a later caveat as
   # a retraction of an earlier positive current-version assertion.
   if NEGATED.search(line[start:match.end()]):continue
   if not refs or any(not 1<=n<=len(evidence) or not verified_current(evidence[n-1]) for n in refs):
    raise ValueError('current_version_not_verified')
