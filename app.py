"""Desktop entry point for Gateway Model Assistant."""
from modern_ui import App, main, messagebox


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='网关模型助手')
    parser.add_argument('--self-test', metavar='REPORT', help='离线验证打包运行环境并写入无凭据报告')
    args = parser.parse_args()
    if args.self_test:
        from app_selftest import run_selftest
        raise SystemExit(run_selftest(args.self_test))
    main()
