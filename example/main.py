from ycappuccino_core import init, start
import logging
import argparse
import sys, os
import inspect
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # Setup logs
    logging.basicConfig(level=logging.INFO)
    path_list = inspect.getfile(sys.modules[__name__]).split(os.sep)[0:-2]
    print(sys.path)
    root_path = "/".join(path_list)
    parser = argparse.ArgumentParser(description='argument for app application')
    parser.add_argument('--root-path', type=str,  help='root path of the application')
    parser.add_argument('--port', default=5000, type=int, help='http port of the application')

    args = parser.parse_args()
    if root_path is None:
        root_path = args.root_path
    init(root_path=root_path, app="test", layers="ycappuccino_service_comm", bundle_prefix="ycappuccino", port=args.port)
    # Run the server
    start()

