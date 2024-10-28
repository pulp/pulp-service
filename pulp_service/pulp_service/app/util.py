def normalize_status(status):
    if 100 <= status < 200:
        return "1xx"
    elif 200 <= status < 300:
        return "2xx"
    elif 300 <= status < 400:
        return "3xx"
    elif 400 <= status < 500:
        return "4xx"
    elif 500 <= status < 600:
        return "5xx"
    else:
        return ""
