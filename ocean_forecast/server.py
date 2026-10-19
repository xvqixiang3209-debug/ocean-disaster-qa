import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from utils import compute_boundary_contour

OUTPUT_DIR = "./output"
DATA_DIR = "./data"

class OceanForecastHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="./frontend", **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/dates":
            self._serve_dates()
            return
        if path == "/api/regions":
            self._serve_regions()
            return
        if path.startswith("/api/data/"):
            date_str = path.split("/")[-1]
            self._serve_date_data(date_str)
            return
        if path.startswith("/api/evolution/"):
            date_str = path.split("/")[-1]
            self._serve_evolution(date_str)
            return
        if path.startswith("/api/forecast/"):
            date_str = path.split("/")[-1]
            self._serve_forecast(date_str)
            return

        super().do_GET()

    def _serve_dates(self):
        """返回所有可用日期列表"""
        dates = []
        for f in sorted(os.listdir(OUTPUT_DIR)):
            if f.endswith("-info.json"):
                date_str = f.replace("-info.json", "")
                dates.append(date_str)
        self._json_response({"dates": dates})

    def _serve_regions(self):
        """返回海区轮廓数据，网格点边界"""
        regions_file = os.path.join(DATA_DIR, "ocean_regions.json")
        if not os.path.exists(regions_file):
            self._json_response({"error": "Not found"}, 404)
            return

        with open(regions_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 为每个海区计算轮廓
        import numpy as np
        result = {}
        for name, info in data['regions'].items():
            points = info['grid_points']
            if len(points) < 3:
                result[name] = {"grid_points": points, "contour": points}
                continue

            contour = compute_boundary_contour(points)

            result[name] = {
                "grid_points": points,
                "contour": contour,
                "color": info['color'],
                "point_count": info['point_count'],
            }

        self._json_response(result)

    def _serve_date_data(self, date_str):
        """返回指定日期的完整数据"""
        path = os.path.join(OUTPUT_DIR, f"{date_str}-info.json")
        if not os.path.exists(path):
            self._json_response({"error": "Not found"}, 404)
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._json_response(data)

    def _serve_evolution(self, date_str):
        """返回指定日期的演变数据"""
        path = os.path.join(OUTPUT_DIR, f"{date_str}-evolution.json")
        if not os.path.exists(path):
            self._json_response({"error": "Not found"}, 404)
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._json_response(data)

    def _serve_forecast(self, date_str):
        path = os.path.join(OUTPUT_DIR, f"{date_str}-forecast.txt")
        if not os.path.exists(path):
            self._json_response({"error": "Not found"}, 404)
            return
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self._text_response(text)

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _text_response(self, text, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def main():
    port = 8765
    server = HTTPServer(("localhost", port), OceanForecastHandler)
    print(f"Ocean Forecast Server running on http://localhost:{port}")
    print(f"Serving frontend from: ./frontend")
    print(f"Serving data from: {OUTPUT_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")

if __name__ == "__main__":
    main()
