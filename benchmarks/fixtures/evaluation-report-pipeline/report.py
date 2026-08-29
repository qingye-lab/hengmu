def publish_report(rows, release_id, storage):
    passed = sum(row["passed"] for row in rows)
    report = {
        "release": release_id,
        "model": "current",
        "passed": passed,
        "attempted": len(rows),
        "prompt": "default",
        "generated_at": "now",
    }
    storage.write(f"reports/{release_id}.json", report)
    return report
