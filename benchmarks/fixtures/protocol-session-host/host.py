ACTIVE_SESSIONS = {}
TOOLS = {"read": lambda arguments: arguments, "publish": lambda arguments: arguments}


def handle(tenant_id, message):
    session = ACTIVE_SESSIONS.setdefault(message["session_id"], {"steps": []})
    session["steps"].append(message)
    tool = TOOLS[message["tool"]]
    return tool(message.get("arguments", {}))
