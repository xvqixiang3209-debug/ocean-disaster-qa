def _detect_grid_step(points):
    if len(points) < 2:
        return 0.25

    p0 = points[0]
    min_dist = float('inf')
    for p in points[1:min(200, len(points))]:
        d = abs(p[0] - p0[0]) + abs(p[1] - p0[1])
        if 0 < d < min_dist:
            min_dist = d

    if min_dist == float('inf'):
        return 0.25

    for step in [0.25, 0.5, 1.0, 2.0, 2.5, 5.0]:
        if abs(min_dist - step) < 0.01:
            return step

    return round(min_dist, 2)


def compute_boundary_contour(points):
    """
    计算边界轮廓，Moore
    """
    if len(points) < 3:
        return [[round(p[0], 2), round(p[1], 2)] for p in points]
    
    point_set = set()
    for p in points:
        point_set.add((round(p[0], 2), round(p[1], 2)))

    step = _detect_grid_step(points)

    # 8方向（E, NE, N, NW, W, SW, S, SE）
    dirs = [
        (0, step),        # E  (0)
        (step, step),     # NE (1)
        (step, 0),        # N  (2)
        (step, -step),    # NW (3)
        (0, -step),       # W  (4)
        (-step, -step),   # SW (5)
        (-step, 0),       # S  (6)
        (-step, step),    # SE (7)
    ]

    # 起点：最北lat最大且最西lon最小的点
    start = max(point_set, key=lambda p: (p[0], -p[1]))
    # 从西侧进入起点
    backtrack_dir = 4

    contour = [start]
    current = start

    max_steps = len(point_set) * 2 + 100
    for _ in range(max_steps):
        # 从 (backtrack_dir + 1) 开始顺时针检查8个邻居
        found = False
        for i in range(8):
            check_dir = (backtrack_dir + 1 + i) % 8
            dl, dn = dirs[check_dir]
            next_p = (round(current[0] + dl, 2), round(current[1] + dn, 2))
            if next_p in point_set:
                if next_p == start and len(contour) > 2:
                    # 回到起点，闭合
                    return [[round(p[0], 2), round(p[1], 2)] for p in contour]
                contour.append(next_p)
                backtrack_dir = (check_dir + 4) % 8  # 反方向
                current = next_p
                found = True
                break

        if not found:
            break
    return [[round(p[0], 2), round(p[1], 2)] for p in contour]