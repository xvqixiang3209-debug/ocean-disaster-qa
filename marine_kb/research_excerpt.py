"""Select complementary original sentences from a research article.

Do not let repeated introduction keywords consume the entire evidence budget
and remove the study area, result, caveat and code/data availability.
"""
import re


FACETS=(
 ('scope',r'Adriatic|Europe|North Sea|Bohai|East China Sea|China|study area|case study|study region|研究区域|渤海|东海|北海|亚得里亚|欧洲'),
 ('inputs',r'tide gauge|altimetr|observations|predictors|ERA5|in situ|输入|验潮|观测'),
 ('method',r'introduces|propose|LSTM|neural network|decoder|Fourier|method|模型|方法'),
 ('forecast_results',r'(?:forecast|lead[- ]time)[\s\S]{0,120}(?:RMSE|MAE|error|improv)|(?:RMSE|MAE|error|improv)[\s\S]{0,120}forecast'),
 ('results',r'achieves|reduction|error|RMSE|MAE|performance|validation|结果|误差|验证'),
 ('limits',r'limitations|limited|underestimat|overestimat|sparse|However|局限|限制|低估'),
 ('code_data',r'Code.{0,10}availability|Data availability|github\.com|zenodo|source code|代码|数据可用'),
)


def research_excerpt(body,limit=2900):
    # English scientific decimals do not end sentences. CJK punctuation and
    # explicit newlines do; keep the original text of every selected span.
    spans=[(m.start(),m[0].strip()) for m in re.finditer(r'.+?(?:[。！？]|\.(?=\s+[A-Z])|\n\s*\n|$)',body,re.S)
           if len(m[0].strip())>=35]
    selected=[];seen=set();used=0
    for facet,pattern in FACETS:
        candidates=[]
        for start,text in spans:
            matches=list(re.finditer(pattern,text,re.I))
            if not matches:continue
            if facet=='forecast_results' and not re.search(r'\d',text):continue
            # References are discovery pointers; prefer actual author prose.
            score=len({m[0].lower() for m in matches})-3*bool(re.search(r'^[^\n]{0,80}et al\.|^https?://doi',text))
            if facet=='results' and re.search(r'\d',text):score+=2
            if facet=='forecast_results':score+=4*bool(re.search(r'\d[\d.]*\s*(?:cm|mm|m\b|%)',text,re.I))
            if facet=='limits':score+=4*bool(re.search(r'beyond.{0,20}\d|diminish|short[- ]lived|outside.{0,20}window',text,re.I))
            if facet=='code_data':
                score+=6*bool(re.search(r'github\.com|zenodo',text,re.I))
                score+=4*bool(re.search(r'Code.{0,10}availability|source code',text,re.I))
            candidates.append((score,start,text,matches[0].start()))
        for score,start,text,first in sorted(candidates,key=lambda r:(-r[0],r[1])):
            if start in seen:continue
            size=min(620,limit-used)
            if size<100:break
            offset=max(0,min(first-80,len(text)-size)) if len(text)>size else 0
            excerpt=text[offset:offset+size]
            selected.append((start,excerpt));seen.add(start);used+=len(excerpt)+2
            break
    if not selected:return body[:limit]
    return '\n\n'.join(text for _,text in sorted(selected))[:limit]
