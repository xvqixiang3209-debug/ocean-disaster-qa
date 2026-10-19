"""
数据处理
"""
import json
import os
import numpy as np
from datetime import datetime
from config import SEA_REGIONS, DATA_CONFIG, WAVE_HEIGHT_MIN_FILTER

class WaveDataLoader:
    def __init__(self):
        self._lats = None
        self._lons = None
        self._lat_start = None
        self._lat_delta = None
        self._lat_size = None
        self._lon_start = None
        self._lon_delta = None
        self._lon_size = None
        self._initialized = False
        self._cache = {}
    
    def _init_grid(self, header):
        """网格信息"""
        lat_seq = header['variables']['lat']['sequence']
        lon_seq = header['variables']['lon']['sequence']
        
        self._lat_start = lat_seq['start']
        self._lat_delta = lat_seq['delta']
        self._lat_size = lat_seq['size']
        self._lon_start = lon_seq['start']
        self._lon_delta = lon_seq['delta']
        self._lon_size = lon_seq['size']
        
        self._lats = np.linspace(
            self._lat_start,
            self._lat_start + self._lat_delta * (self._lat_size - 1),
            self._lat_size
        )
        self._lons = np.linspace(
            self._lon_start,
            self._lon_start + self._lon_delta * (self._lon_size - 1),
            self._lon_size
        )
        self._initialized = True
    
    def load_data(self, utc_time: datetime) -> np.ndarray:
        """加载，返回[lat, lon]"""
        key = utc_time.strftime("%Y%m%d%H%M")
        if key in self._cache:
            return self._cache[key]
        
        filename = f"{key}-sig_height-wave-30m.json"
        filepath = os.path.join(DATA_CONFIG["data_dir"], filename)
        
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Data file not found: {filepath}")
        
        with open(filepath, 'r') as f:
            data = json.load(f)
        
        if not self._initialized:
            self._init_grid(data['header'])
        
        blocks = data['blocks'][0]
        grid = np.array([float(v) for v in blocks]).reshape(
            self._lat_size, self._lon_size
        )
        
        self._cache[key] = grid
        return grid
    
    def interpolate_at_point(self, grid: np.ndarray, lat: float, lon: float) -> float:
        """双线性插值"""
        # 找到最近的4个网格点
        lat_idx = (lat - self._lat_start) / self._lat_delta
        lon_idx = (lon - self._lon_start) / self._lon_delta
        
        lat_i = int(np.floor(lat_idx))
        lon_i = int(np.floor(lon_idx))
        lat_i = max(0, min(lat_i, self._lat_size - 2))
        lon_i = max(0, min(lon_i, self._lon_size - 2))
        
        # 小数
        lat_frac = lat_idx - lat_i
        lon_frac = lon_idx - lon_i
        lat_frac = max(0.0, min(1.0, lat_frac))
        lon_frac = max(0.0, min(1.0, lon_frac))
        
        v00 = grid[lat_i, lon_i]
        v01 = grid[lat_i, lon_i + 1]
        v10 = grid[lat_i + 1, lon_i]
        v11 = grid[lat_i + 1, lon_i + 1]
        
        v0 = v00 * (1 - lon_frac) + v01 * lon_frac
        v1 = v10 * (1 - lon_frac) + v11 * lon_frac
        
        return v0 * (1 - lat_frac) + v1 * lat_frac


class RegionGridBuilder:
    def __init__(self, loader: WaveDataLoader):
        self.loader = loader
        self._region_points = {}
        for name, region in SEA_REGIONS.items():
            self._region_points[name] = region['grid_points']
    
    def get_region_wave_heights(self, grid: np.ndarray, region_name: str) -> np.ndarray:
        """
        获取指定区域在给定数据网格上的浪高值
        """
        points = self._region_points.get(region_name, [])
        if not points:
            return np.array([])
        
        heights = np.array([
            self.loader.interpolate_at_point(grid, lat, lon)
            for lat, lon in points
        ])
        return heights
    
    def get_time_slice_stats(self, utc_time: datetime, region_name: str) -> dict:
        """
        获取指定区域在单个时间切片的浪高统计
        """
        grid = self.loader.load_data(utc_time)
        heights = self.get_region_wave_heights(grid, region_name)
        valid = heights[(heights >= WAVE_HEIGHT_MIN_FILTER) & (heights < 20.0)]
        
        if len(valid) == 0:
            return {"min": 0.0, "max": 0.0, "mean": 0.0, "count": 0}
        
        return {
            "min": float(np.percentile(valid, 5)),
            "max": float(np.percentile(valid, 95)),
            "mean": float(np.mean(valid)),
            "count": len(valid),
        }
    
    def get_day_stats(self, utc_times: list, region_name: str) -> dict:
        """
        每个时间切片取 P5/P95，跨时间切片取极值
        """
        all_mins = []
        all_maxs = []
        
        for t in utc_times:
            try:
                grid = self.loader.load_data(t)
                heights = self.get_region_wave_heights(grid, region_name)
                valid = heights[(heights >= WAVE_HEIGHT_MIN_FILTER) & (heights < 20.0)]
                if len(valid) > 0:
                    all_mins.append(np.percentile(valid, 5))
                    all_maxs.append(np.percentile(valid, 95))
            except Exception as e:
                print(f"  Warning: Failed to load {t} for {region_name}: {e}")
        
        if not all_mins:
            return {"min": 0.0, "max": 0.0, "mean": 0.0, "count": 0}
        
        return {
            "min": float(min(all_mins)),
            "max": float(max(all_maxs)),
            "mean": float(np.mean(all_maxs)),
            "count": len(all_mins),
        }
    
    def get_envelope(self, utc_times: list, region_name: str) -> np.ndarray:
        """
        获取指定区域在多个时间切片上的有效波高包络
        """
        points = self._region_points.get(region_name, [])
        if not points:
            return np.array([])
        
        envelope = np.full(len(points), -999.0)
        
        for t in utc_times:
            try:
                grid = self.loader.load_data(t)
                heights = np.array([
                    self.loader.interpolate_at_point(grid, lat, lon)
                    for lat, lon in points
                ])
                envelope = np.maximum(envelope, heights)
            except Exception:
                pass
        
        return envelope
    
    def get_region_points(self, region_name: str) -> list:
        """获取海区的网格点列表"""
        return self._region_points.get(region_name, [])
