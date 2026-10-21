"""Dimension-aware citation checks for explicit quantities in prose/tables."""
from decimal import Decimal
import re
import unicodedata
import json

UNITS={
    '厘米':('length',Decimal('.01')), 'cm':('length',Decimal('.01')),
    '米':('length',Decimal(1)), 'm':('length',Decimal(1)),
    '千米':('length',Decimal(1000)), '公里':('length',Decimal(1000)), 'km':('length',Decimal(1000)),
    '平方米':('area',Decimal(1)), '平方公里':('area',Decimal(1000000)),
    '平方千米':('area',Decimal(1000000)),
    'm/s':('speed',Decimal(1)), '米/秒':('speed',Decimal(1)), '米每秒':('speed',Decimal(1)),
    'km/h':('speed',Decimal(1)/Decimal('3.6')), '千米/小时':('speed',Decimal(1)/Decimal('3.6')),
    'kt':('speed',Decimal('.514444')), '节':('speed',Decimal('.514444')),
    '℃':('temperature',Decimal(1)), '°C':('temperature',Decimal(1)), '摄氏度':('temperature',Decimal(1)),
    'hPa':('pressure',Decimal(1)), '百帕':('pressure',Decimal(1)),
    '万元':('money',Decimal(10000)), '亿元':('money',Decimal(100000000)), '元':('money',Decimal(1)),
    '%':('percent',Decimal(1)), '人':('people',Decimal(1)), '户':('household',Decimal(1)),
    '小时':('duration',Decimal(3600)), '分钟':('duration',Decimal(60)), '秒':('duration',Decimal(1)),
    'min':('duration',Decimal(60)),
    's':('duration',Decimal(1)), 'second':('duration',Decimal(1)), 'seconds':('duration',Decimal(1)),
    'h':('duration',Decimal(3600)), 'hour':('duration',Decimal(3600)), 'hours':('duration',Decimal(3600)),
    'd':('duration',Decimal(86400)), 'day':('duration',Decimal(86400)), 'days':('duration',Decimal(86400)),
    '天':('duration',Decimal(86400)), '工作日':('workdays',Decimal(1)),
    '升':('volume',Decimal(1)), '毫升':('volume',Decimal('.001')),
    'mg/L':('concentration',Decimal(1)), '毫克/升':('concentration',Decimal(1)),
    'g/L':('concentration',Decimal(1000)), '克/升':('concentration',Decimal(1000)),
    '级':('grade',Decimal(1)),
    '°':('angle',Decimal(1)), '度':('angle',Decimal(1)), 'degree':('angle',Decimal(1)),
}
TOKEN=r'(?<![\d.])([+-]?\d+(?:\.\d+)?)\s*('+ '|'.join(re.escape(k) for k in sorted(UNITS,key=len,reverse=True))+r')(?![A-Za-z/²³]|每秒)'

class CitationQuantityError(ValueError):
    def __init__(self,value,unit,matching,line):
        super().__init__('quantity_unit_not_supported_by_cited_text')
        self.details={'value':str(value),'unit':unit,'allowed_same_dimension':[(str(v),u) for v,u in matching[:12]],'untrusted_line':line[:400]}

def quantities(text):
    text=unicodedata.normalize('NFKC',text).replace('−','-')
    # PDF soft wraps can split a multi-character unit, e.g. 1小\n时.
    # Rejoin only the exact glyphs of declared units, never a number or OCR
    # substitute; units are not guessed from magnitudes or the question.
    for unit in sorted(UNITS,key=len,reverse=True):
        if len(unit)>1 and re.fullmatch(r'[\u4e00-\u9fff]+',unit):
            text=re.sub(r'\s*'.join(map(re.escape,unit)),unit,text)
    text=normalize_grouped_numbers(text)
    # NFKC expands Celsius to °C; map both explicitly.
    rows=[]
    for m in re.finditer(TOKEN,text):
        if m[2]=='户' and re.match(r'主|籍|口',text[m.end():]):
            continue  # NFKC turns an ordinal “①户主申请” into “1户主申请”.
        if m[2]=='节' and re.match(r'选|录|约|气|目|日|课',text[m.end():]):
            continue  # “2021节选” is a document edition, not 2021 knots.
        if m[2]=='节' and re.search(r'第\s*(?:\d+(?:\.\d+)*\s*(?:—|–|~|至|到|-)\s*)?$',text[max(0,m.start()-30):m.start()]):
            continue  # 第6.3节 is an ordinal document locator, never knots.
        value=Decimal(m[1])
        if value>0 and re.search(r'净(?:下降|减少|回落)\s*$',text[max(0,m.start()-8):m.start()]):
            value=-value
        rows.append((value,m[2],m.start(),m.end()))
    for m in re.finditer(r'(?<![\d.])([+-]?\d+(?:\.\d+)?)\s*(?:—|–|~|至|到|-)\s*'+TOKEN,text):
        if m[3]=='节' and re.match(r'选|录|约|气|目|日|课',text[m.end():]):continue
        if m[3]=='节' and re.search(r'第\s*$',text[max(0,m.start()-6):m.start()]):continue
        rows.append((Decimal(m[1]),m[3],m.start(),m.end()))
    return rows


def normalize_grouped_numbers(text):
    # Match the complete grouped integer. A clock suffix such as 14:04,370
    # is two values, not the thousands-separated integer 4,370.
    return re.sub(r'(?<![\d:：.,])([+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?!\d)',
                  lambda match:match[0].replace(',',''),text)


def structured_quantities(text):
    """Read only declared units/known columns of program-generated records."""
    fields={'value_cm':'cm','raw_value_cm':'cm','total_cm':'cm','tide_cm':'cm','swl_cm':'cm',
            '最大总潮位cm':'cm','超过对应阈值cm':'cm','elevation_m':'m','estimated_population':'人',
            '配置的网格同步间隔秒':'秒','maxSpeed':'m/s','moveSpeed':'km/h','pressure':'hPa','wind_kt':'kt',
            '记录距当前小时':'小时','记录距查询时刻小时':'小时','water_temperature_c':'℃',
            'lat':'°','lon':'°','grid_lat':'°','grid_lon':'°'}
    columns={'天文潮cm':'cm','浪高m':'m','风速m/s':'m/s','流速m/s':'m/s'}
    found=[]
    def add(value,unit):
        if type(value) in {int,float} and unit in UNITS:found.append((Decimal(str(value)),unit,0,0))
    def walk(value,inherited=None):
        if isinstance(value,list):
            for row in value:walk(row,inherited)
        elif isinstance(value,dict):
            unit=value.get('unit') or value.get('单位') or inherited
            for key,item in value.items():
                if key in fields:add(item,fields[key])
                elif key.endswith('_cm'):add(item,'cm')
                elif key=='value':add(item,unit)
                walk(item,unit)
            for key,labels in value.items():
                if not key.endswith('列') or not isinstance(labels,list):continue
                for row in value.get(key[:-1],[]) or []:
                    if isinstance(row,list):
                        for label,item in zip(labels,row):
                            if label in columns:add(item,columns[label])
    decoder=json.JSONDecoder();pos=0
    while (start:=text.find('{',pos))>=0:
        try:value,stop=decoder.raw_decode(text,start)
        except ValueError:pos=start+1;continue
        walk(value);pos=stop
    # A selected passage may be a literal slice of a larger JSON record.
    # Explicit schema field names retain their units even without the outer
    # braces. Do not infer units for arbitrary keys or a detached `value`.
    scalar=r'(-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)'
    for field,unit in fields.items():
        for match in re.finditer(r'"'+re.escape(field)+r'"\s*:\s*'+scalar+r'(?![\d.])',text):
            found.append((Decimal(match[1]),unit,match.start(),match.end()))
    for match in re.finditer(r'"[A-Za-z_\u4e00-\u9fff]{1,64}_cm"\s*:\s*'+scalar+r'(?![\d.])',text):
        found.append((Decimal(match[1]),'cm',match.start(),match.end()))
    return found


def table_quantities(text):
    """Bind numeric cells to an explicit unit-only table header.

    PDF tables often render `m/s kt km/h` on one line and wind ranges on
    the following rows. No unit is inferred from an ID or a bare number.
    """
    return [(value,unit,0,0) for _,unit,relation,_,_ in _source_table_cells(text)
            for value in relation if isinstance(value,Decimal)]


def symbol_quantities(text):
    """Bind inequality endpoints only to an explicitly defined table symbol.

    A header such as 有效波高（米） and the declaration Hs为有效波高
    define the units of 4.0≤Hs<6.0. Bare numbers elsewhere in that page
    remain untyped. OCR guesses (约/臆 for inequality signs) are not repaired.
    """
    text=unicodedata.normalize('NFKC',text)
    units='|'.join(re.escape(u) for u in sorted(UNITS,key=len,reverse=True))
    headings={m[1]:m[2] for m in re.finditer(r'([\u4e00-\u9fff]{2,12})\s*\(('+units+r')\)',text)}
    result=[]
    for definition in re.finditer(r'\b([A-Za-z][A-Za-z0-9_]{0,8})\s*(?:为|表示)\s*([\u4e00-\u9fff]{2,12})',text):
        symbol,label=definition.groups();unit=headings.get(label)
        if unit is None:continue
        variable=r'(?<![A-Za-z0-9_])'+re.escape(symbol)+r'(?![A-Za-z0-9_])'
        number=r'([+-]?\d+(?:\.\d+)?)';op=r'(?:≤|≥|<|>|=|<=|>=)'
        for pattern in (number+r'\s*'+op+r'\s*'+variable,variable+r'\s*'+op+r'\s*'+number):
            for match in re.finditer(pattern,text):
                result.append((Decimal(match[1]),unit,match.start(),match.end()))
    return result

def known_quantities(evidence):
    result=[]
    for row in evidence:
        source=str(row.get('text') or '')
        # A header or symbol definition belongs to its own document. Joining
        # documents here could silently lend another source its missing unit.
        result.extend(quantities(source)+table_quantities(source)+symbol_quantities(source))
        if row.get('metadata',{}).get('source_type') in {'business_forecast','system_runtime'}:
            result.extend(structured_quantities(row.get('text') or ''))
    return result


def supports_quantity(value,unit,known):
    category,factor=UNITS[unit]
    return any(UNITS[u][0]==category and abs(value*factor-v*UNITS[u][1])<=max(factor,UNITS[u][1])*Decimal('.005')
               for v,u,_,_ in known)


def explicit_quantity_supports_number(line,start,number,evidence):
    """Let the literal-number gate defer exact, explicitly unit-bound values.

    Position matters: 30 minutes cannot provide evidence for 30 people on the
    same line. Unknown JSON units are not sufficient to defer this gate.
    """
    begin=start-1 if start and line[start-1] in '+-' else start
    token=line[begin:begin+80]
    typed=[row for row in quantities(token) if row[2]==0 and abs(row[0])==Decimal(number)]
    known=known_quantities(evidence)
    return any(supports_quantity(v,u,known) for v,u,_,_ in typed)


def validate_quantity_units(answer,evidence):
    for line in unicodedata.normalize('NFKC',answer).replace('−','-').splitlines():
        refs=[int(n) for n in re.findall(r'\[资料(\d+)\]',line)]
        if not refs:continue  # the caller separately rejects uncited quantities
        known=known_quantities([evidence[n-1] for n in refs if 1<=n<=len(evidence)])
        for value,unit,_,_ in quantities(line):
            category,factor=UNITS[unit]
            matching=[(v,u) for v,u,_,_ in known if UNITS[u][0]==category]
            # Structured JSON stores some units separately; its typed delivery
            # facts are checked independently. Do not invent a unit association.
            if not matching:
                raise CitationQuantityError(value,unit,matching,line)
            allowed=supports_quantity(value,unit,known)
            if not allowed:raise CitationQuantityError(value,unit,matching,line)


def validate_markdown_tables(answer,evidence):
    """Validate bare numeric cells using explicit column units and row citations."""
    units=[]
    for line in unicodedata.normalize('NFKC',answer).splitlines():
        if not line.lstrip().startswith('|'):
            units=[];continue
        cells=[c.strip() for c in line.strip().strip('|').split('|')]
        if cells and all(re.fullmatch(r'[:\s-]+',cell or '-') for cell in cells):continue
        found=[]
        for cell in cells:
            matches=[u for u in sorted(UNITS,key=len,reverse=True)
                     if re.search(r'(?<![A-Za-z/])'+re.escape(u)+r'(?![A-Za-z/])',cell)]
            found.append(matches[0] if matches else None)
        if not any(re.search(r'\d',cell) for cell in cells):
            units=found;continue
        # Some headings contain digits, e.g. a 2-minute average. They have no
        # numeric cells by themselves and still introduce their column units.
        numeric=[bool(re.fullmatch(r'[<>≤≥+\-\d.\s~～—–至到及以上下]+',re.sub(r'\[资料\d+\]','',cell))) for cell in cells]
        if not any(numeric):
            units=found;continue
        if not units or not any(units):continue
        refs=re.findall(r'\[资料(\d+)\]',line)
        if not refs:raise ValueError('uncited_quantity')
        citation=''.join('[资料'+n+']' for n in refs)
        for cell,unit,is_numeric in zip(cells,units,numeric):
            if not unit or not is_numeric:continue
            clean=re.sub(r'\[资料\d+\]','',cell).strip()
            interval=re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*(?:~|～|—|–|至|到|-)\s*([+-]?\d+(?:\.\d+)?)',clean)
            values=interval.groups() if interval else re.findall(r'[+-]?\d+(?:\.\d+)?',clean)
            for value in values:
                validate_quantity_units(value+' '+unit+citation,evidence)


def declared_column_unit(label):
    """Read a literal column unit, excluding a mean period such as ``2 min``."""
    label=unicodedata.normalize('NFKC',re.sub(r'\[资料\d+\]','',label)).replace('**','').strip()
    if label in UNITS:return label
    unit_pattern='|'.join(re.escape(u) for u in sorted(UNITS,key=len,reverse=True))
    found=re.findall(r'(?:\(|[,，;；])\s*('+unit_pattern+r')(?=\s*[),，;；])',label)
    # Standards also declare dimensional columns as 角度/° or 风速/(m/s).
    # Require the complete, known unit at the end of this column label. An
    # internal slash in a physical unit must never become another delimiter
    # (e.g. treating the /s in an unlabelled m/s as a seconds column).
    slash=re.search(r'(?<![A-Za-z])[/／]\s*(?:\(\s*)?('+unit_pattern+r')\s*\)?\s*$',label)
    if slash:found.append(slash[1])
    return found[0] if len(set(found))==1 else None


_CELL_NUMBER=r'[+-]?\d+(?:\.\d+)?'


def _cell_relation(cell,unit):
    """Keep exact interval inclusivity; never reduce a range to a number set."""
    cell=unicodedata.normalize('NFKC',re.sub(r'\[资料\d+\]','',cell)).replace('−','-').replace('**','').strip()
    cell=re.sub(r'\s+','',cell)
    if unit and cell.endswith(unit):cell=cell[:-len(unit)]
    elif unit:
        cell=re.sub(re.escape(unit)+r'(?=及以上$|以上$|及以下$|以下$)','',cell)
    cell=cell.replace('不小于','≥').replace('不少于','≥').replace('不大于','≤').replace('不超过','≤')
    cell=cell.replace('大于','>').replace('小于','<').replace('>=','≥').replace('<=','≤')
    match=re.fullmatch(r'([\[(])('+_CELL_NUMBER+r')[,，]('+_CELL_NUMBER+r')([\])])',cell)
    if match:return ('range',Decimal(match[2]),match[1]=='[',Decimal(match[3]),match[4]==']')
    suffix=re.fullmatch('('+_CELL_NUMBER+r')(?:及以上|以上|及以下|以下)',cell)
    if suffix:cell=('≥' if cell.endswith('以上') else '≤')+suffix[1]
    cell=re.sub(r'级$','',cell) if unit=='级' else cell
    match=re.fullmatch('('+_CELL_NUMBER+r')(?:~|～|—|–|至|到|-)('+_CELL_NUMBER+')',cell)
    if match:return ('range',Decimal(match[1]),True,Decimal(match[2]),True)
    match=re.fullmatch(r'([<>≤≥])('+_CELL_NUMBER+')',cell)
    if match:
        op,value=match[1],Decimal(match[2])
        return ('range',value,op=='≥',None,False) if op in {'>','≥'} else ('range',None,False,value,op=='≤')
    match=re.fullmatch('('+_CELL_NUMBER+r')([<≤])[A-Za-z][A-Za-z0-9_]*([<≤])('+_CELL_NUMBER+')',cell)
    if match:return ('range',Decimal(match[1]),match[2]=='≤',Decimal(match[4]),match[3]=='≤')
    if re.fullmatch(_CELL_NUMBER,cell):return ('value',Decimal(cell))
    return None


def explicit_cell_unit(cell):
    """Only a fully parsed quantity with its own literal unit, not a guess."""
    clean=unicodedata.normalize('NFKC',re.sub(r'\[资料\d+\]','',cell)).replace('**','').strip()
    for unit in sorted(UNITS,key=len,reverse=True):
        if re.search(re.escape(unit)+r'(?=\s*(?:及以上|以上|及以下|以下)?$)',clean) and _cell_relation(cell,unit):
            return unit
    return None


def _scaled_relation(relation,unit):
    dimension,factor=UNITS[unit]
    return (dimension,)+tuple(item*factor if isinstance(item,Decimal) else item for item in relation)


def _row_identity(label):
    # Only typography and an explicit station suffix are ignored. In particular
    # 台风, 强台风 and 超强台风 are different row identities.
    label=unicodedata.normalize('NFKC',re.sub(r'\[资料\d+\]','',label)).replace('**','')
    label=re.sub(r'\s+','',label).strip(' :：')
    return label[:-1] if label.endswith('站') else label


def _averaging_periods(text):
    result=[]
    for match in re.finditer(r'('+_CELL_NUMBER+r')\s*(分钟|min|秒|s|小时|h)\s*(?:平均|mean|average)',text,re.I):
        result.append(Decimal(match[1])*UNITS[match[2]][1])
    return tuple(result)


def _column_identity(label):
    label=unicodedata.normalize('NFKC',re.sub(r'\[资料\d+\]','',label)).replace('**','')
    label=re.sub(r'\([^)]*\)','',label)
    # Averaging duration is checked separately against the source table scope.
    # It is not part of the physical column name (风速 vs 2分钟平均风速).
    label=re.sub(r'\d+(?:\.\d+)?\s*(?:分钟|min|秒|s|小时|h)\s*平均','',label)
    label=re.sub(r'\s+|(?:为|是|达到|达|约)$','',label).strip(' :：')
    return '' if label in UNITS else label


def _same_column(expected,actual):
    if actual is None:return False  # repeated unlabeled units are ambiguous
    if not actual or not expected:return True
    # Known extra description around an otherwise identical column is harmless;
    # an explicit maximum/minimum distinction must remain attached to its value.
    if any(a in expected and b in actual or b in expected and a in actual
           for a,b in [('最大','最小'),('最高','最低')]):return False
    for markers in [('最大','最高'),('最小','最低')]:
        if any(m in expected for m in markers) and not any(m in actual for m in markers):return False
    if expected==actual or expected in actual or actual in expected:return True
    # These are table-label qualifiers, not aliases between physical fields.
    strip=lambda text:re.sub(r'对应|底层中心附近|中心附近|附近|底层|最大|最小|最高|最低|平均|范围|等级|的','',text)
    return bool(strip(expected)) and strip(expected)==strip(actual)


def _source_table_cells(source):
    """Extract row-bound values from explicit Markdown or PDF unit columns."""
    rows=[];units=[];pdf_units=[];pdf_labels=0;pdf_seen=False;context=[];scope=();pdf_scope=();columns=[];pdf_columns=[]
    for raw in unicodedata.normalize('NFKC',source).replace('−','-').splitlines():
        line=raw.strip()
        if not line:continue
        if line.startswith('|'):
            cells=[c.strip() for c in line.strip('|').split('|')]
            if all(re.fullmatch(r'[:\s-]+',c or '-') for c in cells):continue
            declared=[declared_column_unit(c) for c in cells]
            if any(declared) and not any(_cell_relation(c,u) for c,u in zip(cells,declared) if u):
                units=declared;columns=[_column_identity(c) for c in cells];scope=_averaging_periods(line);continue
            if len(units)==len(cells):
                label=next((c for c,u in zip(cells,units) if not u and c),None)
                if label:
                    for cell,unit,column in zip(cells,units,columns):
                        rel=_cell_relation(cell,unit) if unit else None
                        if rel:rows.append((_row_identity(label),unit,rel,scope,column))
            continue
        units=[]
        if re.match(r'^表\s*\d',line):context=[]
        context=(context+[line])[-6:]
        pieces=line.split()
        if pieces and all(p in UNITS for p in pieces):
            pdf_units=pieces;pdf_labels=0;pdf_seen=False;pdf_scope=_averaging_periods('\n'.join(context))
            description=next((_column_identity(c) for c in reversed(context[:-1]) if re.search(r'风速|水位|潮位|浪高|波高|深度|人数|面积',c)), '')
            pdf_columns=[description if pieces.count(u)==1 else None for u in pieces];continue
        single_unit=declared_column_unit(line)
        if single_unit and not re.search(r'\d',line) and not pdf_units:
            pdf_units=[single_unit];pdf_columns=[_column_identity(line)];pdf_labels=0;pdf_seen=False;pdf_scope=_averaging_periods('\n'.join(context));continue
        if not pdf_units:continue
        match=re.fullmatch(r'([^\d|<>≤≥]+?)\s+(.+)',line)
        values=re.sub(r'([<>≤≥])\s+(?=\d)',r'\1',match[2]).split() if match else []
        relations=[_cell_relation(cell,unit) for cell,unit in zip(values,pdf_units)]
        if match and len(values)==len(pdf_units) and all(relations):
            rows.extend((_row_identity(match[1]),unit,rel,pdf_scope,column) for unit,rel,column in zip(pdf_units,relations,pdf_columns))
            pdf_seen=True;continue
        if not pdf_seen and pdf_labels<4 and len(line)<45 and not re.search(r'\d',line):
            extra=declared_column_unit(line)
            if extra:pdf_units.append(extra);pdf_columns.append(_column_identity(line))
            pdf_labels+=1;continue
        pdf_units=[]
    return rows


def _prose_row_cells(source,label):
    """Read quantities only in the named row's short explicit prose clause."""
    source=unicodedata.normalize('NFKC',source).replace('−','-')
    identity=re.escape(label)
    # A Chinese prefix prevents matching 台风 inside 强台风. A short explicit
    # property clause is required; unrelated following sentences never supply
    # this row's units or endpoints.
    pattern=r'(?<![\w\u4e00-\u9fff])'+identity+r'(?:站)?(?=\s|[为是:：,，(]|最大|最小|平均|有效|水位|风速|风力|浪高|高度|人数|数量|面积|深度)'
    rows=[]
    unit_pattern='|'.join(re.escape(u) for u in sorted(UNITS,key=len,reverse=True))
    numeric=r'(?:[<>≤≥]?\s*'+_CELL_NUMBER+r'(?:\s*(?:~|～|—|–|至|到|-)\s*'+_CELL_NUMBER+r')?(?:及以上|以上|及以下|以下)?)'
    for found in re.finditer(pattern,source):
        tail=re.split(r'[。；;，,\n]',source[found.end():],maxsplit=1)[0][:180]
        previous=_averaging_periods(source[max(0,found.start()-200):found.start()])
        own=_averaging_periods(tail)
        scope=own or previous[-1:]
        for match in re.finditer('('+numeric+r')\s*('+unit_pattern+r')(?![A-Za-z/])',tail):
            rel=_cell_relation(match[1],match[2])
            if rel:rows.append((label,match[2],rel,scope,_column_identity(tail[:match.start()])))
    return rows


def validate_recovered_table_row(header,line,evidence):
    """Require the same row, dimensional column and complete range/boundary.

    This stricter check applies to recovery of a rejected numeric table. The
    caller supplies only the exact selected passages, not whole documents.
    """
    labels=[c.strip() for c in header.strip().strip('|').split('|')]
    cells=[c.strip() for c in line.strip().strip('|').split('|')]
    if len(labels)!=len(cells):raise ValueError('table_column_count_mismatch')
    units=[declared_column_unit(c) for c in labels]
    # A cell such as ``8~9级`` explicitly declares its own unit even when the
    # header says only 风力等级. It still needs the same row/column/boundary;
    # leaving it to a document-wide number bag would allow another grade.
    for index,(cell,unit) in enumerate(zip(cells,units)):
        if unit:continue
        units[index]=explicit_cell_unit(cell)
    columns=[_column_identity(column) for column in labels]
    for index,(column,unit) in enumerate(zip(columns,units)):
        if not unit or not re.fullmatch(r'(?:依据|适用|判定)?(?:范围|区间)',column):continue
        # A generic 区间 column can name the sole other explicit physical
        # quantity of this dimensional type in the same answer table. Multiple
        # fields (e.g. water level and wave height) stay ambiguous and rejected.
        peers={other for j,(other,u) in enumerate(zip(columns,units)) if j!=index and u
               and UNITS[u][0]==UNITS[unit][0] and other
               and not re.fullmatch(r'(?:依据|适用|判定)?(?:范围|区间)',other)}
        if len(peers)==1:columns[index]=next(iter(peers))
    numeric=[(cell,unit,_cell_relation(cell,unit),column) for cell,unit,column in zip(cells,units,columns) if unit]
    if any(re.search(r'\d',re.sub(r'\[资料\d+\]','',cell)) and relation is None
           for cell,_,relation,_ in numeric):
        raise ValueError('table_numeric_cell_not_parsed')
    numeric=[row for row in numeric if row[2]]
    if not numeric:return
    identity=next((_row_identity(c) for c,u in zip(cells,units) if not u and re.sub(r'\[资料\d+\]','',c).strip()),None)
    if not identity:raise ValueError('table_row_identity_missing')
    mean_periods=_averaging_periods(unicodedata.normalize('NFKC',header))
    known=[]
    for source in evidence:
        text=str(source.get('text') or '')
        known.extend(_source_table_cells(text))
        known.extend(_prose_row_cells(text,identity))
    for _,unit,relation,column in numeric:
        expected=_scaled_relation(relation,unit)
        if not any(name==identity and _scaled_relation(actual,actual_unit)==expected
                   and all(period in scope for period in mean_periods)
                   and _same_column(column,source_column)
                   for name,actual_unit,actual,scope,source_column in known):
            raise ValueError('table_row_or_boundary_not_supported')
