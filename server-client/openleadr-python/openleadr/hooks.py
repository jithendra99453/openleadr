import asyncio

HOOKS = {'before_parse': [],
         'before_handle': [],
         'after_handle': [],
         'before_respond': []}


def register(hook_point, callback):
    """
    Call a hook
    """
    if hook_point not in HOOKS:
        raise ValueError(f"""The hook_point must be one of '{', '.join(HOOKS.keys())}', """
                         f"""you provided '{hook_point}'""")
    HOOKS[hook_point].append(callback)


def call(hook_point, *args, **kwargs):
    loop = asyncio.get_event_loop()
    for hook in HOOKS.get(hook_point, []):
        loop.create_task(hook(*args, **kwargs))
