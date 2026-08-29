SHARED = {}


def classify(request, tools):
    SHARED[request["id"]] = {"route": request["text"].split()[0]}
    return tools[request["requested_tool"]](request)


def resolve(request, tools):
    state = SHARED[request["id"]]
    state["result"] = tools[request["requested_tool"]](request)
    return state
