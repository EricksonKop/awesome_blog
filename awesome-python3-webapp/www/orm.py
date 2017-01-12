#!user/bin/env python3
# -*- coding: utf-8 -*-
import logging;logging.basicConfig(level=logging.INFO)
import aiomysql
import asyncio

__author__ = "Erickson"


def log(sql, args=None):
    logging.info('SQL: [%s] args: %s' % (sql, args or []))


async def create_pool(loop, **kw):  # create connection pool
    logging.info('create database connection pool...')
    global __pool
    __pool = await aiomysql.create_pool(
        loop=loop,                              # 传递消息循环对象loop用于异步执行
        user=kw['user'],
        password=kw['password'],
        db=kw['db'],
        host=kw.get('host', 'localhost'),
        port=kw.get('port', 3306),              # 默认mysql端口是3306
        charset=kw.get('charset', 'utf8'),      # 默认数据库字符集是utf8
        autocommit=kw.get('autocommit', True),  # 默认自动提交事务
        maxsize=kw.get('maxsize', 10),          # 连接池最多同时处理10个请求
        minsize=kw.get('minsize', 1)            # 连接池最少1个请求
    )


# 用于SQL的SELECT语句，对应select方法，传入sql语句和参数
async def select(sql, args, size=None):
    log(sql, args)  # incoming sql query and arguments
    # 异步连接对象返回可以连接线程，with语句封装了关闭conn和处理异常的工作
    async with __pool.get() as conn:
        # DictCursor可将结果以dict形式返回，通过游标对象执行SQL
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql.replace('?', '%s'), args or ())
            # 这里返回的是由tuple组成的list，每个tuple里的元素为dict形式
            if size:
                rs = await cur.fetchmany(size)   # 从数据库获取指定行数的结果
            else:
                rs = await cur.fetchall()        # 返回所有结果集
        logging.info('rows returned: %s' % len(rs))
        # return dict within a list
        return rs


# 用于SQL的INSERT, UPDATE, DELETE的语句，只返回结果数，不返回结果集
async def execute(sql, args, autocommit=True):
    log(sql, args)
    async with __pool.get() as conn:
        if not autocommit:  # 若设置不是自动提交，则手动开启事务
            await conn.begin()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql.replace('?', '%s'), args)
                affected = cur.rowcount    # 返回受影响的行数
            if not autocommit:
                await conn.commit()
        except BaseException as e:
            if not autocommit:
                await conn.rollback()  # 出错则回滚到增删改之前
            raise e
        return affected


class Field(object):  # 保存数据库的字段名和字段类型等
    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default

    def __str__(self):
        return '<%s, %s:%s>' % (self.__class__.__name__, self.column_type, self.name)


class StringField(Field):
    def __init__(self, name=None, primary_key=False, default=None, ddl='varchar(100)'):
        super().__init__(name, ddl, primary_key, default)


class IntegerField(Field):
    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)


class BooleanField(Field):
    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)


class FloatField(Field):
    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)


# 这个不能作为主键的对象，所以直接设定为False
class TextField(Field):
    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)


# 这是一个元类，定义了创建类时的行为，任何定义了__metaclass__属性或指定了metaclass的都会通过元类定义的构造方法构造类
# 任何继承自Model的类，都会自动通过ModelMetaclass扫描映射关系，并存储到自身的类属性
class ModelMetaclass(type):

    def __new__(cls, name, bases, attrs):
        # cls: 当前准备创建的类对象，相当于self
        # name：类名
        # bases：父类的tuple
        # attrs：属性(方法)的dict，比如User有id等，就作为attrs的keys
        # 排除Model类本身，因为Model类主要是用来被继承的，不存在与数据库表的映射
        if name == 'Model':
            return type.__new__(cls, name, bases, attrs)
        # 获取表名，若没有__table__属性，将类名作为表名
        tableName = attrs.get('__table__', name)
        logging.info('found model: %s (table: %s)' % (name, tableName))
        # 建立映射关系表并找到主键
        mappings = dict()       # 用于保存映射关系
        escaped_fields = []     # 用于保存所有非主键字段名
        primaryKey = None      # 保存主键

        # 遍历类的属性，找出定义的域(如StringField,字符串域)内的值，建立映射关系
        # k是属性名，v是定义域
        for k, v in attrs.copy().items():  # 从类属性中获取所有的Field属性
            if isinstance(v, Field):
                logging.info('  found mapping: %s ==> %s' % (k, v))
                # 把Field属性类保存在映射关系表，并从原属性列表删除
                mappings[k] = attrs.pop(k)
                # 查找并检验主键是否唯一，主键初始值为None，找到主表键后设置为k
                if v.primary_key:
                    if primaryKey:
                        raise StandardError('Duplicate primary key for field: %s' % k)
                    primaryKey = k
                else:
                    escaped_fields.append(k)
        if not primaryKey:                # 没有找到主键也将报错
            raise StandardError('Primary key not found.')
        # 创建新的类属性
        attrs['__mappings__'] = mappings  # 映射关系表
        attrs['__table__'] = tableName
        attrs['__primary_key__'] = primaryKey  # 主键属性名
        attrs['__fields__'] = escaped_fields + [primaryKey]  # 所有属性名添加进__fields__属性
        # 构造默认的SELECT, INSERT，UPDATE及DELETE语句
        attrs['__select__'] = 'select * from `%s`' % (tableName)
        attrs['__insert__'] = 'insert into `%s` (%s) values(%s)' % (tableName, ','.join('%s' % f for f in mappings), ','.join('?'*len(mappings)))
        attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (tableName, ','.join('`%s`=?' % f for f in escaped_fields), primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls, name, bases, attrs)


# ORM映射基类，继承自dict，通过ModelMetaclass元类来构造类
class Model(dict, metaclass=ModelMetaclass):
    def __init__(self, **kw):        # 初始化函数，调用其父类dict的方法
        super(Model, self).__init__(**kw)

    # 当以a.b方式调用不存的属性时，解释器会试图调用该方法获取属性
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError("'Model' object has no attribute '%s'" % key)

    # 可通过a.b=c的形式设置属性
    def __setattr__(self, key, value):
        self[key] = value

    def getValue(self, key):
        return getattr(self, key, None)

    # 通过键来取值，若值不存在，则取默认值
    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if value is None:
            field = self.__mappings__[key]
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' % (key, value))
                # 通过default取到值后再将其作为当前值
                setattr(self, key, value)
        return value

    # classmethod装饰器将方法定义为类方法
    # 对于查询相关的操作，都定义为类方法，可方便查询，不必先创建实例再查询
    @classmethod
    async def findAll(cls, where=None, args=None, **kw):  # 查询所有符合条件的信息
        ' find objects by where clause. '
        sql = [cls.__select__]
        if args is None:
            args = []
        # WHERE查找条件的关键字
        if where:
            sql.append('where %s' % where)
        # ORDER BY是排序的关键字
        if kw.get('orderBy') is not None:
            sql.append('order by %s' % (kw['orderBy']))
        # LIMIT是筛选结果集的关键字
        limit = kw.get('limit', None)
        if limit is not None:
            # limit仅有一个参数时，表示从第一行开始的操作行数
            if isinstance(limit, int):
                sql.append('limit ?')
                args.append(limit)
            # limit为两个参数时，前一个表示开始操作的行数(第一行为0)，后一个是操作行数的数目(-1表示到最后记录行)
            elif isinstance(limit, tuple) and len(limit) == 2:
                sql.append('limit ?, ?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value: %s' % str(limit))
        rs = await select(' '.join(sql), args)    # 调用前面定义的select函数，没有指定size，因此会fetchall
        return [cls(**r) for r in rs]             # 返回结果是各实例对象(行)组成的list

    # 根据列名和条件查看数据库有多少条信息
    @classmethod
    async def findNumber(cls, selectField='*', where=None, args=None):
        ' find number by select and where.'
        sql = ['select count(%s) _num_ from `%s`' % (selectField, cls.__table__)]
        if where:
            sql.append('where %s' % where)
        rs = await select(' '.join(sql), args, 1)
        if not rs:
            return 0
        return rs[0]['_num_']

    # 根据主键查找一个实例的信息
    @classmethod
    async def find(cls, pk):
        ' find objects by primary key.'
        rs = await select('%s where `%s`=?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(rs) == 0:
            return None
        return cls(**rs[0])

    # 把一个实例保存到数据库
    async def save(self):
        args = list(map(self.getValueOrDefault, self.__mappings__))
        rows = await execute(self.__insert__, args)
        if rows != 1:
            logging.warn('failed to insert record: affected rows: %s' % rows)

    # 更改一个实例在数据库的信息
    async def update(self):
        args = list(map(self.getValue, self.__fields__))
        rows = await execute(self.__update__, args)
        if rows != 1:
            logging.warn('failed to update by primary key: affected rows: %s' % rows)

    # 把一个实例从数据库删除
    async def remove(self):
        args = list(self.getValue(self.__primary_key__))
        rows = await execute(self.__delete__, args)
        if rows != 1:
            logging.warn('failed to delete by primary key: affected rows: %s' % rows)
