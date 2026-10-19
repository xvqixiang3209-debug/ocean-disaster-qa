"""
后端
"""
import json
import os
import sys
from flask import Flask, request, Response, jsonify, send_from_directory, stream_with_context
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
import threading

from config import (
    BASE_DIR, FRONTEND_DIR, OCEAN_FORECAST_DIR, OCEAN_FORECAST_OUTPUT,
    OCEAN_FORECAST_DATA, SYSTEM_PROMPTS, load_region_mapping, load_region_info,
    load_ocean_regions, TIANDITU_API_KEY,
)
from intent import recognize_intent
from task_processor import (
    process_query,
    process_judgment_advice,
    process_sea_area_forecast,
    process_typhoon_analogue,
)
from model_layer import call_model, get_model_list
from ocean_api import get_location_data
from typhoon.api import create_typhoon_blueprint
from typhoon.bootstrap import build_typhoon_components
from typhoon.general_web_qa import GeneralWebQuestionService
from forecast_products import create_blueprint as create_forecast_products_blueprint

# 初始化数据
try:
    region_mapping = load_region_mapping()
except (FileNotFoundError, json.JSONDecodeError) as exc:
    print(f"[Startup Warning] 近岸地点映射不可用: {exc}")
    region_mapping = {}
try:
    region_info = load_region_info()
except (FileNotFoundError, json.JSONDecodeError) as exc:
    print(f"[Startup Warning] 地点资料不可用: {exc}")
    region_info = {}

typhoon_components = build_typhoon_components()
typhoon_knowledge_base = typhoon_components.repositories.historical
typhoon_service = typhoon_components.service
typhoon_application = typhoon_components.application
current_typhoon_provider = typhoon_components.current_provider
general_web_qa = GeneralWebQuestionService(
    settings=typhoon_components.settings,
    web_search=typhoon_components.web_search,
    web_reader=typhoon_components.conversations.web_reader,
    web_evidence=typhoon_components.conversations.web_evidence,
)

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
from forecast_runtime.api import install as install_forecast_runtime
forecast_runtime = install_forecast_runtime(app)
forecast_runtime.analogue_service = typhoon_components.service
from forecast_runtime.sync import LiveTyphoonProvider
from dataclasses import replace
typhoon_components = replace(typhoon_components, current_provider=LiveTyphoonProvider(forecast_runtime.sync))
current_typhoon_provider = typhoon_components.current_provider
app.register_blueprint(create_typhoon_blueprint(typhoon_components))
from marine_kb.bootstrap import install as install_marine_knowledge
marine_qa = install_marine_knowledge(app, typhoon_components, general_web_qa, call_model)
marine_qa.forecast_data = forecast_runtime
from marine_kb.chat_api import create_chat_blueprint
app.register_blueprint(create_chat_blueprint(marine_qa, typhoon_components))
app.register_blueprint(create_forecast_products_blueprint(lambda: app.extensions["marine_kb"]["store"].database))
# Explicit application worker, not a desktop automation. Flask test clients do not start it.
if os.getenv('MARINE_FORECAST_WORKER', 'false').lower() == 'true':
    forecast_runtime.start_worker()

# 前端
@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get('/chat')
def simple_chat():
    return send_from_directory(FRONTEND_DIR, "chat.html")


@app.get('/map')
def advanced_map():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/api/map/config")
def map_config():
    """Return the public browser-side basemap configuration.

    TianDiTu WMTS loads directly in the user's browser and therefore its token
    cannot be treated as a server secret. Keep the DeepSeek credential out of
    this response and restrict the map token in the provider console.
    """
    response = jsonify({
        "provider": "tianditu",
        "configured": bool(TIANDITU_API_KEY),
        "api_key": TIANDITU_API_KEY,
        "layers": ["vec_w", "cva_w"],
    })
    response.headers["Cache-Control"] = "no-store"
    return response


# 对话
@app.route("/api/chat", methods=["POST"])
def chat():
    """流式对话接口"""
    body = request.get_json(force=True)
    user_message = body.get("message", "").strip()
    model_name = body.get("model", "deepseek-v4-flash")

    if not user_message:
        return jsonify({"error": "消息不能为空"}), 400

    # 意图识别
    intent_result = recognize_intent(user_message, region_mapping)
    intent = intent_result.get("intent", "other")
    print(f"[Intent] {intent} | raw: {intent_result}")

    # 根据意图处理任务
    prompt_key = None
    formatted_message = user_message
    error_msg = None
    deterministic_response = None

    if intent == "query":
        locations = intent_result.get("locations", [])
        if not locations:
            intent = "other"
        else:
            prompt_key, formatted_message = process_query(locations, user_message, region_mapping)

    elif intent == "judgment_advice":
        locations = intent_result.get("locations", [])
        if not locations:
            intent = "other"
        else:
            prompt_key, formatted_message = process_judgment_advice(
                locations, user_message, region_mapping, region_info
            )

    elif intent == "sea_area_forecast":
        date_str = intent_result.get("date", "today")
        prompt_key, formatted_message = process_sea_area_forecast(date_str)
        if prompt_key is None:
            error_msg = formatted_message
            intent = "other"

    elif intent == "typhoon_analogue":
        prompt_key = "typhoon_analogue"
        try:
            analysis_result, deterministic_response = process_typhoon_analogue(
                typhoon_application, current_typhoon_provider, model_name=model_name
            )
            used_mode = analysis_result.get("report_meta", {}).get("used_mode", "template")
            formatted_message = (
                "台风历史类比排名由本地纯路径算法完成，强度仅用于结果解释；"
                f"报告模式为{used_mode}。"
            )
        except Exception as exc:
            error_msg = str(exc)

    # 系统提示词
    if intent == "other" and not error_msg:
        prompt_key = "marine_rag"

    general_web_result = None
    if intent == "other" and not error_msg:
        try:
            general_web_result = marine_qa.answer(user_message, model_name=model_name,
                allow_web=os.getenv("MARINE_WEB_ENABLED", "false").lower() == "true")
        except Exception as exc:
            error_msg = f"专业知识问答失败（{type(exc).__name__}）"

    system_prompt = SYSTEM_PROMPTS.get(prompt_key, SYSTEM_PROMPTS["other"])

    # 调用模型
    def generate():
        # 先发送元数据
        meta = {
            "intent": intent,
            "prompt_key": prompt_key,
            "formatted_message": formatted_message,
            "model": model_name,
        }
        if general_web_result is not None:
            meta.update(general_web_result.get("answer_meta") or {})
        if error_msg:
            yield f"data: {json.dumps({'type': 'meta', **meta}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'error', 'content': error_msg}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'type': 'meta', **meta}, ensure_ascii=False)}\n\n"

        if deterministic_response is not None:
            yield f"data: {json.dumps({'type': 'content', 'content': deterministic_response}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            return

        if general_web_result is not None:
            yield f"data: {json.dumps({'type': 'content', 'content': general_web_result.get('answer') or ''}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'sources', 'items': general_web_result.get('web_sources') or []}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'rag_sources', 'items': general_web_result.get('rag_sources') or []}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            return

        try:
            stream = call_model(model_name, system_prompt, formatted_message, stream=True)
            for text_chunk in stream:
                yield f"data: {json.dumps({'type': 'content', 'content': text_chunk}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': f'模型调用失败: {str(e)}'}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/models", methods=["GET"])
def models():
    """获取可用模型列表"""
    return jsonify({"models": get_model_list()})

# 海区预报
@app.route("/api/dates")
def api_dates():
    """获取可用日期列表"""
    dates = []
    if os.path.exists(OCEAN_FORECAST_OUTPUT):
        for fname in os.listdir(OCEAN_FORECAST_OUTPUT):
            if fname.endswith("-info.json"):
                date_str = fname.replace("-info.json", "")
                dates.append(date_str)
    dates.sort()
    return jsonify({"dates": dates})


@app.route("/api/regions")
def api_regions():
    """获取海区轮廓数据（网格点边界，Moore边界追踪算法计算轮廓）"""
    regions_path = os.path.join(OCEAN_FORECAST_DATA, "ocean_regions.json")
    if not os.path.exists(regions_path):
        return jsonify({"error": "regions file not found"}), 404

    with open(regions_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 计算轮廓
    sys.path.insert(0, OCEAN_FORECAST_DIR)
    from ocean_forecast.utils import compute_boundary_contour
    result = {}
    for name, info in data["regions"].items():
        points = info["grid_points"]
        if len(points) < 3:
            result[name] = {"grid_points": points, "contour": points}
            continue

        contour = compute_boundary_contour(points)

        result[name] = {
            "grid_points": points,
            "contour": contour,
            "color": info["color"],
            "point_count": info["point_count"],
        }

    return jsonify(result)


@app.route("/api/data/<date_str>")
def api_date_data(date_str):
    """获取指定日期的海区数据"""
    info_path = os.path.join(OCEAN_FORECAST_OUTPUT, f"{date_str}-info.json")
    if os.path.exists(info_path):
        with open(info_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    return jsonify({"error": "data not found"}), 404


@app.route("/api/evolution/<date_str>")
def api_evolution(date_str):
    """获取指定日期的大浪区演变数据"""
    evo_path = os.path.join(OCEAN_FORECAST_OUTPUT, f"{date_str}-evolution.json")
    if os.path.exists(evo_path):
        with open(evo_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    return jsonify({"error": "evolution not found"}), 404


@app.route("/api/forecast/<date_str>")
def api_forecast(date_str):
    """获取指定日期的预报文本"""
    txt_path = os.path.join(OCEAN_FORECAST_OUTPUT, f"{date_str}-forecast.txt")
    if os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8") as f:
            text = f.read()
        return Response(text, mimetype="text/plain; charset=utf-8")
    return jsonify({"error": "forecast not found"}), 404


@app.route("/api/region_mapping")
def api_region_mapping():
    """获取近岸海域映射表（地点列表）"""
    return jsonify(region_mapping)


def main():
    port = 8766
    print(f"running on http://localhost:{port}")
    print(f"Frontend: {FRONTEND_DIR}")
    print(f"Ocean forecast data: {OCEAN_FORECAST_OUTPUT}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

if __name__ == "__main__":
    main()
