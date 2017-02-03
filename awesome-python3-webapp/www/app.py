#!user/bin/env python3
# -*- coding: utf-8 -*-
import logging; logging.basicConfig(level=logging.INFO)
import asyncio, os, json, time
from datetime import datetime
from aiohttp import web
from jinja2 import Environment, FileSystemLoader
from config import configs
import orm
from urllib import parse
from coroweb import add_routes, add_static
from handlers import cookie2user, COOKIE_NAME


def init_jinja2(app, **kw):
    logging.info('init jinja2...')  # 初始化模板引擎
    options = dict(
        autoescape=kw.get('autoescape', True),  # 自动转义?
        block_start_string=kw.get('block_start_string', '{%'),
        block_end_string=kw.get('block_end_string', '%}'),
        variable_start_string=kw.get('variable_start_string', '{{'),
        variable_end_string=kw.get('variable_end_string', '}}'),
        auto_reload=kw.get('auto_reload', True)  # loader checks if the template resources change
    )
    path = kw.get('path', None)
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath('__file__')), 'templates')
    logging.info('set jinja2 template path: %s' % path)
    env = Environment(loader=FileSystemLoader(path), **options)  # 创建模板环境
    filters = kw.get('filters', None)
    if filters is not None:
        for k, v in filters.items():
            env.filters[k] = v  # 把键值对注册到模板环境的filters字典上
    app['__templating__'] = env  #


async def logger_factory(app, handler):
    async def logger(request):
        logging.info('Request: %s, %s' % (request.method, request.path))
        return await handler(request)
    return logger


async def auth_factory(app, handler):
    async def auth(request):
        logging.info('check user: %s %s' % (request.method, request.path))
        request.__user__ = None
        cookie_str = request.cookies.get(COOKIE_NAME)
        if cookie_str:
            user = await cookie2user(cookie_str)
            if user:
                logging.info('set current user: %s' % user.email)
                request.__user__ = user
        if request.path.startswith('/manage/') and (request.__user__ is None or not request.__user__.admin):
            return web.HTTPFound('/signin')
        return await handler(request)
    return auth


async def data_factory(app, handler):
    # 把请求数据绑定到request的__data__属性上
    async def parse_data(request):
        logging.info('data_factory...')
        if request.method in ('POST', 'PUT'):
            if not request.content_type:
                return web.HTTPBadRequest('Missing Content-Type.')
            content_type = request.content_type.lower()
            if content_type.startswith('application/json'):
                # reads request body decoded as json
                request.__data__ = await request.json()
                if not isinstance(request.__data__, dict):
                    return web.HTTPBadRequest('JSON body must be object.')
                logging.info('request json: %s' % request.__data__)
            elif content_type.startswith(('application/x-www-form-urlencoded', 'multipart/form-data')):
                # a multidict with all the variables in the POST parameters
                params = await request.post()
                request.__data__ = dict(**params)
                logging.info('request form: %s' % request.__data__)
            else:
                return web.HTTPBadRequest('Unsupported Content-Type: %s' % content_type)
        elif request.method == 'GET':
            qs = request.query_string
            # 把给定的字符串参数解析并把数据以字典形式返回
            request.__data__ = {k: v[0] for k, v in parse.parse_qs(qs, True).items()}
            logging.info('request query: %s' % request.__data__)
        else:
            request.__data__ = dict()
        return await handler(request)
    return parse_data


async def response_factory(app, handler):  # 响应报文后处理
    async def response(request):
        logging.info('Response handler...')
        r = await handler(request)
        if isinstance(r, web.StreamResponse):
            return r
        if isinstance(r, bytes):
            resp = web.Response(body=r)
            resp.content_type = 'application/octet-stream'
            return resp
        if isinstance(r, str):
            if r.startswith('redirect:'):
                return web.HTTPFound(r[9:])
            resp = web.Response(body=r.encode('utf-8'))
            resp.content_type = 'text/html;charset=utf-8'
            return resp
        if isinstance(r, dict):
            template = r.get('__template__')
            if template is None:
                resp = web.Response(body=json.dumps(r, ensure_ascii=False, default=lambda o: o.__dict__).encode('utf-8'))
                resp.content_type = 'application/json;charset=utf-8'
                return resp
            else:
                resp = web.Response(body=app['__templating__'].get_template(template).render(**r).encode('utf-8'))
                resp.content_type = 'text/html;charset=utf-8'
                return resp
        if isinstance(r, int) and r > 100 and r < 600:
            return web.Response(r)
        if isinstance(r, tuple) and len(r) == 2:
            t, m = r
            if isinstance(t, int) and t > 100 and t < 600:
                return web.Response(t, str(m))
        # default
        resp = web.Response(body=str(r).encode('utf-8'))
        resp.content_type = 'text/plain;charset=utf-8'
        return resp
    return response


def datetime_filter(t):
    delta = int(time.time() - t)
    if delta < 60:
        return u'1分钟前'
    if delta < 3600:
        return u'%s分钟前' % (delta // 60)
    if delta < 86400:
        return u'%s小时前' % (delta // 3600)
    if delta < 604800:
        return u'%s天前' % (delta // 86400)
    dt = datetime.fromtimestamp(t)
    return u'%s%年s月%s日' % (dt.year, dt.month, dt.day)


async def init(loop):
    # 初始化连接池
    await orm.create_pool(loop=loop, **configs.db)
    # 建立Application服务器应用对象
    app = web.Application(loop=loop, middlewares=[logger_factory, auth_factory, data_factory, response_factory])
    # 初始化模板环境
    init_jinja2(app, filters=dict(datetime=datetime_filter))
    add_routes(app, 'handlers')  # 批量加载路由
    add_static(app)  # 添加静态文件
    # make_handler: creates HTTP protocol factory for handling requests
    srv = await loop.create_server(app.make_handler(), '127.0.0.1', 9000)
    logging.info('server started at http://127.0.0.1:9000')
    return srv

loop = asyncio.get_event_loop()
loop.run_until_complete(init(loop))
loop.run_forever()
