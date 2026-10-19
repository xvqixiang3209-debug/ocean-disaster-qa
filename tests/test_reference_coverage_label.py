import copy
import pytest
from marine_kb.qa import _version_only_reference_coverage


def fixture():
 contract={'mode':'method_or_knowledge','explicit_date_requested':False,'specific_measurement_requested':False}
 part={'id':1,'question':'12395是什么电话？','citations':[1]}
 matrix={'reference_citations':[1],'candidate_citations':[]}
 app={'use':'reference','temporal':'version_unverified','object_match':'matched','named_object_match':'matched',
      'geography':'unspecified','reasons':['尚未核实文件生效及修订废止关系']}
 return contract,part,matrix,[{'metadata':{'applicability':app}}]


def test_only_unverified_edition_can_downgrade_metadata_instead_of_losing_answer():
 assert _version_only_reference_coverage(*fixture())


@pytest.mark.parametrize('change',[
 lambda c,p,m,e:c.update(mode='current_or_historical_fact'),
 lambda c,p,m,e:c.update(explicit_date_requested=True),
 lambda c,p,m,e:c.update(specific_measurement_requested=True),
 lambda c,p,m,e:e[0]['metadata']['applicability'].update(use='excluded'),
 lambda c,p,m,e:e[0]['metadata']['applicability'].update(temporal='after_cutoff'),
 lambda c,p,m,e:e[0]['metadata']['applicability'].update(geography='mismatched'),
 lambda c,p,m,e:e[0]['metadata']['applicability'].update(named_object_match='mismatched'),
 lambda c,p,m,e:e[0]['metadata']['applicability'].update(reasons=['尚未核实文件生效及修订废止关系','与所问地点不符']),
 lambda c,p,m,e:p.update(citations=[2]),
])
def test_live_data_or_other_applicability_conflicts_are_never_downgraded_away(change):
 args=fixture();change(*args)
 assert not _version_only_reference_coverage(*args)
