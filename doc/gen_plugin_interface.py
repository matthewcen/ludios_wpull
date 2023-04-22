#!/usr/bin/env python3
import inspect
import os

import wpull.application.builder
import wpull.application.plugin


def main():
    assert wpull.application.builder

    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'scripting_interfaces_include.rst'
    )
    print(path)

    with open(path, 'w') as out_file:
        out_file.write('.. This document was automatically generated.\n')
        out_file.write('   DO NOT EDIT!\n\n')

        items = sorted(
            wpull.application.plugin.global_interface_registry.items(),
            key=lambda item: item[0].value
        )

        for hook_name, (function, category) in items:
            hook_name_str = inspect.getmodule(hook_name).__name__ + '.' + str(hook_name)
            function_name_str = inspect.getmodule(function).__name__ + '.' + function.__qualname__

            out_file.write(
                f':py:attr:`{hook_name} <{hook_name_str}>`\n'
            )
            out_file.write(
                f'   {category.value} Interface: :py:meth:`{function.__qualname__} <{function_name_str}>`\n\n'
            )


if __name__ == '__main__':
    main()
