空间查询模块（2026-10-17）

准备：项目venv python tools/prepare_spatial_data.py
该操作索引24幅已下载ASTER GDEM V3；将PopSE2020全国人口按118,27,124,32包络，裁出保持源像元对齐的Albers窗口。保存data/spatial_runtime/index.json、prepared/population_envelope_sourcegrid.tif和verification_queries.json。
范围是覆盖浙江的矩形包络，并非浙江省界，不宜称为“浙江人口合计”。

集成：from spatial_runtime import SpatialRuntime
runtime = SpatialRuntime(app_root)
runtime.status()
runtime.point(lon, lat)
runtime.population_bbox(west, south, east, north)
查询不下载数据、不调用模型、不写状态。首次prepare需要本地原始数据。

人口统计保持原始100米源像元人数；不重采样、不简单对人口密度求和。查询按源像元中心落在半开矩形west<=lon<east,south<=lat<north统计。边界像元不按面积分摊。0是有效值，32767是NoData。裁切前后人数、有效像元、缺测像元和极值完全一致才发布index。
保留精确原WKT：Albers，105度中央经线，25/47度标准纬线，Krassovsky1942椭球，D_unknown基准。经纬度变换包含未核定的基准关系，其绝对定位精度未知；投影往返校验仅验证数学变换，不能验证基准转换准确性。
人口数据是2020年估计常住人口，并非2026年实际在场人数，不能当作需转移人数或精确受灾人数。

地形以WGS84经纬度按原始像元取值；高程EGM96、米，ASTER产品定义NoData=-9999，海平面水体0有效。原TIFF未编码垂直CRS，采用产品说明并保留来源。未将EGM96转换到1985国家高程基准，不与潮位直接相减推导精确淹没，亦不从海面0值推定海底水深、滩涂出露或通行安全。
ASTER产品说明：https://doi.org/10.5067/ASTER/ASTGTM.003
原始数据指南：https://www.earthdata.nasa.gov/s3fs-public/2025-04/ASTGTM_User_Guide_V3.pdf
人口来源：https://doi.org/10.6084/m9.figshare.24916140.v1

依赖：rasterio、pyproj（仅经官方PyPI安装）；依赖约束见本目录requirements.txt。
