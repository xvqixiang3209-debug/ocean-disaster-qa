from marine_kb.research_excerpt import research_excerpt


def test_analysis_and_forecast_scores_remain_separate_original_spans():
 body=('Introduction: storm surge research is useful.\n\n'*70+
       'The study area is the North Sea, using tide gauge observations.\n\n'
       'The method uses variational assimilation and adaptive error covariance.\n\n'
       'Removing tide gauges for analysis validation improves RMSE by up to 16 cm at some locations.\n\n'
       'Simulated forecast experiments show RMSE improvements of up to 5 cm for the first 24 h of forecasting.\n\n'
       'Beyond 24 h, improvements quickly diminish however.\n\n'
       'Code availability: https://github.com/author/project provides the implementation.\n\n')
 result=research_excerpt(body)
 assert 'analysis validation' in result and '16 cm' in result
 assert '5 cm for the first 24 h' in result and 'Beyond 24 h' in result
 assert 'github.com/author/project' in result and len(result)<=2900
 assert all(span in body for span in result.split('\n\n'))
