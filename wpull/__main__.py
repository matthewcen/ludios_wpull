# encoding=utf-8
import os
import time

import wpull.application.main


if __name__ == '__main__':
    if os.environ.get('RUN_PROFILE'):
        import cProfile
        cProfile.run('wpull.application.main()', f'stats-{int(time.time())}.profile')
    elif os.environ.get('RUN_PDB'):
        import pdb

        def wrapper():
            wpull.application.main.main(exit=False)
            pdb.set_trace()

        pdb.runcall(wrapper)
    else:
        wpull.application.main.main()
