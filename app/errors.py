"""Safe page errors; API and infrastructure routes retain FastAPI JSON errors."""
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException


PAGE_ERRORS = {
    401: ('請先登入', '登入狀態可能已失效。請重新登入，再回到文件知識庫。'),
    403: ('這次操作未完成', '操作驗證未通過。請回到文件知識庫重新開啟頁面，再試一次；若持續發生，請重新登入。'),
    404: ('找不到文件或頁面', '連結可能已失效，或目前帳號無法閱讀。請回到文件知識庫，查看你可以開啟的文件。'),
    409: ('文件版本已變更', '這次變更尚未儲存。請回到文件知識庫，重新開啟最新的分享設定，確認後再送出。'),
    422: ('請檢查表單內容', '這次操作未完成。請回到文件知識庫重新開啟表單，確認欄位與選取項目後再送出。'),
}


def _under(path, prefix):
    return path == prefix or path.startswith(prefix + '/')


def _accept_quality(accept, target):
    """An explicit media range overrides a wildcard, including q=0."""
    best_specificity, quality = -1, 0.0
    for item in accept.lower().split(','):
        media, *parameters = [part.strip() for part in item.split(';')]
        specificity = 2 if media == target else (1 if media == target.split('/')[0] + '/*'
                                                  else (0 if media == '*/*' else -1))
        if specificity < 0:
            continue
        value = 1.0
        for parameter in parameters:
            if parameter.startswith('q='):
                try:
                    value = float(parameter[2:])
                except ValueError:
                    value = 0.0
                if not 0 <= value <= 1:
                    value = 0.0
        if specificity > best_specificity:
            best_specificity, quality = specificity, value
        elif specificity == best_specificity:
            quality = max(quality, value)
    return quality


def _wants_html(request):
    path = request.url.path
    if any(_under(path, prefix) for prefix in
           ('/api', '/static', '/health', '/docs', '/redoc', '/openapi.json')):
        return False
    page_route = path in ('/', '/login', '/logout', '/demo') or _under(path, '/documents')
    accept = request.headers.get('accept', '*/*')
    html = _accept_quality(accept, 'text/html')
    json = _accept_quality(accept, 'application/json')
    # Unknown browser links get a useful routing 404; wildcard clients keep JSON.
    explicit_html = any(item.split(';')[0].strip().lower() == 'text/html'
                        for item in accept.split(','))
    return html > 0 and html >= json and (page_route or explicit_html)


def install_error_handlers(app, templates):
    def render(request, status, headers=None):
        title, message = PAGE_ERRORS[status]
        # Deliberately no identity/session lookup, exception detail or request URL.
        return templates.TemplateResponse(request, 'error.html', {
            'me': None, 'status': status, 'error_title': title, 'error_message': message,
        }, status_code=status, headers=headers)

    @app.exception_handler(HTTPException)
    async def page_http_error(request, error):
        if error.status_code in PAGE_ERRORS and _wants_html(request):
            response = render(request, error.status_code, error.headers)
        else:
            response = await http_exception_handler(request, error)
        response.headers.append('Vary', 'Accept')
        return response

    @app.exception_handler(RequestValidationError)
    async def page_validation_error(request, error):
        if _wants_html(request):
            response = render(request, 422)
        else:
            response = await request_validation_exception_handler(request, error)
        response.headers.append('Vary', 'Accept')
        return response
