def export_trace(request, client):
    span = {
        "task": request["task_id"],
        "prompt": request["prompt"],
        "authorization": request.headers.get("Authorization"),
        "tool_output": request["tool_output"],
    }
    client.send(span)
