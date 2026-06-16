type JsonPrimitive = str | int | float | bool | None
type JsonArray = list[JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonValue = JsonPrimitive | JsonArray | JsonObject
type RawQueqiaoPayload = JsonValue | bytes | bytearray
