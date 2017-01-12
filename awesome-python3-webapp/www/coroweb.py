#!user/bin/env python3
# -*- coding: utf-8 -*-
import logging;logging.basicConfig(level=logging.INFO)
import inspect, asyncio, os, functools
from aiohttp import web
from errors import APIError


def request(path, *, method):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        wrapper.__path__ = path
        wrapper.__method__ = method
        return wrapper
    return decorator

get = functools.partial(request, method='GET')
post = functools.partial(request, method='POST')
put = functools.partial(request, method='PUT')
delete = functools.partial(request, method='DELETE')


class RequestHandler(object):

    def __init__(self, func):
        self._func = asyncio.coroutine(func)

    async def __call__(self, request):
        required_args = inspect.signature(self._func).parameters
        logging.info('required args: %s' % required_args)
        # 对GET和POST进来的参数值装进kw字典里
        kw = {arg: value for arg, value in request.__data__.items() if arg in required_args}
        # 对match_info里参数值用update方法
        kw.update(request.match_info)
        # 对request参数单独装进字典
        if 'request' in required_args:
            kw['request'] = request
        # 参数检查
        for name, arg in required_args.items():
            if name == 'request' and arg.kind in (arg.VAR_POSITIONAL, arg.VAR_KEYWORD):
                return HTTPBadRequest('request param cannot be the variable argument.')
            # 非变长参数是不可缺省的
            if arg.kind not in (arg.VAR_POSITIONAL, arg.VAR_KEYWORD):
                if arg.default == arg.empty and arg.name not in kw:
                    return HTTPBadRequest('Missing argument: %s' % arg.name)

        logging.info('Call with args: %s' % kw)
        try:
            return await self._func(**kw)
        except APIError as e:
            return dict(error=e.error, data=e.data, message=e.message)


# 添加一个模块的所有路由
def add_routes(app, module_name):
    try:
        n = module_name.rfind('.')
        if n == (-1):
            mod = __import__(module_name, globals(), locals())
        else:
            name = module_name[n+1:]
            mod = getattr(__import__(module_name[:n], globals(), locals(), [name]), name)
    except ImportError as e:
        raise e
    for attr in dir(mod):
        if attr.startswith('_'):
            continue
        fn = getattr(mod, attr)
        if callable(fn) and hasattr(fn, '__method__') and hasattr(fn, '__path__'):
            args = ','.join(inspect.signature(fn).parameters.keys())
            logging.info('Add %s,%s => %s(%s)...' % (fn.__method__, fn.__path__, fn.__name__, args))
            app.router.add_route(fn.__method__, fn.__path__, RequestHandler(fn))


# 添加静态文件夹的路径
def add_static(app):  # adds a router and a handler for returning static files
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    app.router.add_static('/static/', path)
    logging.info('add static %s => %s' % ('/static/', path))
