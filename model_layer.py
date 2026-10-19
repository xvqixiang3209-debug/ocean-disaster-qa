"""
模型层
"""
import json
try:
    from openai import OpenAI
except ImportError:  # Typhoon matching and its deterministic report do not need this SDK.
    OpenAI = None
from config import MODELS, SYSTEM_PROMPTS


def call_model(
    model_name,
    system_prompt,
    user_message,
    stream=True,
    *,
    temperature=0.7,
    top_p=0.8,
    timeout=None,
    max_retries=2,
    thinking=None,
    response_format=None,
    max_tokens=None,
):
    """
    调用指定模型进行对话
    返回: generator (stream=True) 或 str (stream=False)
    """
    model_config = MODELS.get(model_name)
    if not model_config:
        raise ValueError(f"Unknown model: {model_name}")
    if OpenAI is None:
        raise RuntimeError("未安装 openai SDK；台风类比功能可独立使用，其他问答请先安装 requirements.txt")
    if not model_config.get("api_key") or not model_config.get("base_url") or not model_config.get("code"):
        raise RuntimeError(f"模型 {model_name} 尚未在 config.py 中配置完整")

    client_options = {
        "api_key": model_config["api_key"],
        "base_url": model_config["base_url"],
        "max_retries": max_retries,
    }
    if timeout is not None:
        client_options["timeout"] = timeout
    client = OpenAI(
        **client_options,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    extra_body = {}
    completion_options = {}
    if response_format is not None:
        completion_options['response_format'] = response_format
    if max_tokens is not None:
        completion_options['max_tokens'] = max_tokens
    if model_config["provider"] == "dashscope":
        extra_body["enable_thinking"] = False
    elif model_config["provider"] == "deepseek" and thinking in {"enabled", "disabled"}:
        extra_body["thinking"] = {"type": thinking}

    if stream:
        response = client.chat.completions.create(
            model=model_config["code"],
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            stream=True,
            extra_body=extra_body if extra_body else None,
            **completion_options,
        )
        return _stream_generator(response)
    else:
        response = client.chat.completions.create(
            model=model_config["code"],
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            stream=False,
            extra_body=extra_body if extra_body else None,
            **completion_options,
        )
        return response.choices[0].message.content


def _stream_generator(response):
    """将流式响应转换为文本生成"""
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def get_model_list():
    """获取可用模型"""
    return list(MODELS.keys())
