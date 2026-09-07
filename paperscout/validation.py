"""模型输出是外部输入；结构错误必须带字段位置暴露给编排层。"""

class OutputError(ValueError):
    pass


def object_value(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise OutputError(f"{field}: 需要 JSON 对象")
    return value


def list_value(value, field: str) -> list:
    if not isinstance(value, list):
        raise OutputError(f"{field}: 需要列表")
    return value


def text_value(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OutputError(f"{field}: 需要非空字符串")
    return value.strip()


def strings(value, field: str, maximum: int | None = None) -> list[str]:
    items = list_value(value, field)
    result = [text_value(item, f"{field}[{i}]") for i, item in enumerate(items)]
    return result if maximum is None else result[:maximum]


def subtopic(value, field: str = "subtopic", max_queries: int = 4) -> dict:
    obj = object_value(value, field)
    result = {**obj, "name": text_value(obj.get("name"), f"{field}.name")}
    result["queries"] = strings(obj.get("queries"), f"{field}.queries", max_queries)
    if not result["queries"]:
        raise OutputError(f"{field}.queries: 至少一个查询")
    for key in ("scope", "exclude", "rationale"):
        if not isinstance(obj.get(key, ""), str):
            raise OutputError(f"{field}.{key}: 需要字符串")
        result[key] = obj.get(key, "")
    return result
