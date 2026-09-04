import json
import sys

def main():
    try:
        sys.stdin.read()
    except Exception:
        pass
    
    response = {
        "injectSteps": [
            {
                "ephemeralMessage": "CRITICAL REMINDER: Always check for any running tasks using the manage_task tool (Action='list'). If any task is not running intentionally, kill it."
            }
        ]
    }
    
    print(json.dumps(response))

if __name__ == "__main__":
    main()
