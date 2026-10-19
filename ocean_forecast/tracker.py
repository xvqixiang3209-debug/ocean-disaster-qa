"""
大浪区追踪
"""
import numpy as np
from collections import defaultdict
from config import (
    SEA_REGIONS, TRACKING_CONFIG, DAY23_REGIONS,
    REGION_DISPLAY_ORDER, MERGE_RULES, ADJACENT_GROUPS,
    get_wave_level
)
from utils import compute_boundary_contour

class BigWaveTracker:  
    def __init__(self, loader, builder):
        self.loader = loader
        self.builder = builder
        self.threshold = TRACKING_CONFIG["big_wave_threshold"]  # 2.5m
        self.min_area_ratio = TRACKING_CONFIG["min_area_ratio"]  # 10%
    
    def compute_region_big_wave(self, utc_times, region_name):
        """
        计算单个海区的大浪包络统计
        返回: dict with has_big_wave, height_min, height_max, big_wave_ratio, envelope
        """
        envelope = self.builder.get_envelope(utc_times, region_name)
        if len(envelope) == 0:
            return {"has_big_wave": False, "height_min": 0, "height_max": 0, "big_wave_ratio": 0}
        
        # 有效波高过滤
        valid_mask = (envelope >= 0.5) & (envelope < 20.0)
        total_valid = np.sum(valid_mask)
        
        if total_valid == 0:
            return {"has_big_wave": False, "height_min": 0, "height_max": 0, "big_wave_ratio": 0}
        
        # 掩码
        big_wave_mask = envelope >= self.threshold
        big_wave_count = np.sum(big_wave_mask)
        big_wave_ratio = big_wave_count / len(envelope)
        
        if big_wave_count == 0 or big_wave_ratio < self.min_area_ratio:
            return {"has_big_wave": False, "height_min": 0, "height_max": 0, "big_wave_ratio": big_wave_ratio}
        
        # 浪高范围
        big_wave_heights = envelope[big_wave_mask]
        height_min = float(np.min(big_wave_heights))
        height_max = float(np.max(big_wave_heights))
        
        return {
            "has_big_wave": True,
            "height_min": round(height_min, 1),
            "height_max": round(height_max, 1),
            "big_wave_ratio": big_wave_ratio,
            "envelope": envelope,
            "big_wave_mask": big_wave_mask,
        }
    
    def compute_day_big_waves(self, utc_times, day_num):
        """
        返回: dict {region_name: big_wave_info}
        """
        regions_to_check = DAY23_REGIONS if day_num >= 2 else list(SEA_REGIONS.keys())
        
        results = {}
        for region_name in regions_to_check:
            # 展开合并名称
            actual_regions = self._expand_region_name(region_name)
            for r in actual_regions:
                if r in SEA_REGIONS:
                    info = self.compute_region_big_wave(utc_times, r)
                    results[r] = info
        
        return results
    
    def _expand_region_name(self, name):
        for merged, parts in MERGE_RULES.items():
            if name == merged:
                return parts
        return [name]
    
    def track_day23(self, day1_times, day2_times, day3_times):
        """
        返回: day2_systems, day3_systems
        day2 与 day1 比较，day3 与 day2 比较
        """
        # 计算三天的大浪情况
        day1_bw = self.compute_day_big_waves(day1_times, 1)
        day2_bw = self.compute_day_big_waves(day2_times, 2)
        day3_bw = self.compute_day_big_waves(day3_times, 3)

        # 确定哪些海区需要追踪
        tracking_regions = set()
        for r in DAY23_REGIONS:
            tracking_regions.update(self._expand_region_name(r))

        day2_systems = self._build_day_systems(day1_bw, day2_bw, tracking_regions, 2)
        day3_systems = self._build_day_systems(day2_bw, day3_bw, tracking_regions, 3)

        return day2_systems, day3_systems
    
    def _build_day_systems(self, day1_bw, dayN_bw, tracking_regions, day_num):
        systems = []
        seen_regions = set()
        
        # 按从北到南的顺序处理
        for region_name in REGION_DISPLAY_ORDER:
            if region_name not in tracking_regions:
                continue
            if region_name in seen_regions:
                continue
            
            d1 = day1_bw.get(region_name, {})
            dN = dayN_bw.get(region_name, {})
            
            d1_has = d1.get("has_big_wave", False)
            dN_has = dN.get("has_big_wave", False)
            
            if not dN_has:
                continue
            
            seen_regions.add(region_name)
            
            # 确定状态
            if d1_has:
                status = "维持"
            else:
                status = "生成"
            display_regions = self._get_display_regions(region_name, dayN_bw, tracking_regions)
            for r in display_regions:
                seen_regions.add(r)
            
            h_min = dN["height_min"]
            h_max = dN["height_max"]
            level_text = self._get_level_text(h_min, h_max)
            
            systems.append({
                "system_id": len(systems) + 1,
                "status": status,
                "regions": display_regions,
                "display_regions": self._format_display_regions(display_regions, status),
                "height_min": h_min,
                "height_max": h_max,
                "level_text": level_text,
            })
        
        for region_name in REGION_DISPLAY_ORDER:
            if region_name not in tracking_regions:
                continue
            if region_name in seen_regions:
                continue
            
            d1 = day1_bw.get(region_name, {})
            dN = dayN_bw.get(region_name, {})
            
            d1_has = d1.get("has_big_wave", False)
            dN_has = dN.get("has_big_wave", False)
            
            if d1_has and not dN_has:
                seen_regions.add(region_name)
                display_regions = self._get_display_regions(region_name, day1_bw, tracking_regions)
                for r in display_regions:
                    seen_regions.add(r)
                
                h_min = d1["height_min"]
                h_max = d1["height_max"]
                level_text = self._get_level_text(h_min, h_max)
                
                systems.append({
                    "system_id": len(systems) + 1,
                    "status": "减弱消失",
                    "regions": display_regions,
                    "display_regions": self._format_display_regions(display_regions, "减弱消失"),
                    "height_min": h_min,
                    "height_max": h_max,
                    "level_text": level_text,
                })
        
        return systems
    
    def _get_display_regions(self, region_name, day_bw, tracking_regions):
        display = [region_name]
        
        # 检查东海合并
        if region_name == "东海北部" and "东海南部" in tracking_regions:
            if day_bw.get("东海南部", {}).get("has_big_wave", False):
                display.append("东海南部")
        elif region_name == "东海南部" and "东海北部" in tracking_regions:
            if day_bw.get("东海北部", {}).get("has_big_wave", False):
                display.insert(0, "东海北部")
        
        # 检查钓鱼岛附近海域
        if "东海南部" in display and "钓鱼岛附近海域" in tracking_regions:
            if day_bw.get("钓鱼岛附近海域", {}).get("has_big_wave", False):
                display.append("钓鱼岛附近海域")
        
        return display
    
    def _format_display_regions(self, regions, status):
        if not regions:
            return []
        
        # 东海北部+东海南部东海
        has_north = "东海北部" in regions
        has_south = "东海南部" in regions
        if has_north and has_south:
            result = ["东海"]
            for r in regions:
                if r not in ["东海北部", "东海南部"]:
                    result.append(r)
            return result
        
        return list(regions)
    
    def _get_level_text(self, h_min, h_max):
        """获取浪级描述"""
        level_min = get_wave_level(h_min)
        level_max = get_wave_level(h_max)
        if level_min == level_max:
            return level_min
        return f"{level_min}到{level_max}"
    
    def compute_evolution(self, evolution_times):
        """
        计算演变数据
        返回: list of time slice data
        """
        tracking_regions = set()
        for r in DAY23_REGIONS:
            tracking_regions.update(self._expand_region_name(r))
        
        evolution = []
        for t in evolution_times:
            slice_data = {
                "time": t.strftime("%Y-%m-%d %H:%M"),
                "time_bj": (t + __import__('datetime').timedelta(hours=8)).strftime("%m-%d %H:%M"),
                "systems": [],
            }
            
            for region_name in REGION_DISPLAY_ORDER:
                if region_name not in tracking_regions:
                    continue
                
                # 获取该时间切片的浪高
                try:
                    grid = self.loader.load_data(t)
                    heights = self.builder.get_region_wave_heights(grid, region_name)
                    valid = heights[(heights >= 0.5) & (heights < 20.0)]
                    
                    if len(valid) == 0:
                        continue
                    
                    big_mask = heights >= self.threshold
                    big_count = np.sum(big_mask)
                    
                    if big_count == 0:
                        continue
                    
                    big_ratio = big_count / len(heights)
                    if big_ratio < self.min_area_ratio:
                        continue
                    
                    big_heights = heights[big_mask]
                    
                    # 计算轮廓
                    contour = self._compute_contour(region_name, big_mask)
                    
                    slice_data["systems"].append({
                        "region": region_name,
                        "height_min": round(float(np.min(big_heights)), 1),
                        "height_max": round(float(np.max(big_heights)), 1),
                        "area_ratio": round(float(big_ratio), 3),
                        "contour": contour,
                    })
                except Exception as e:
                    pass
            
            evolution.append(slice_data)
        
        return evolution
    
    def _compute_contour(self, region_name, big_wave_mask):
        """
        返回排序后的边界点[[lat, lon], ...]
        """
        points = self.builder.get_region_points(region_name)
        if len(points) == 0:
            return []

        big_points = [points[i] for i in range(len(points)) if big_wave_mask[i]]

        if len(big_points) < 3:
            return [[p[0], p[1]] for p in big_points]

        return compute_boundary_contour(big_points)
