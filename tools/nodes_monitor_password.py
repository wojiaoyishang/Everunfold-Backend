"""Generate an independent node monitor password hash from terminal input."""

from getpass import getpass

from app.modules.auth.security import hash_password


def main() -> None:
    first = getpass("节点监控密码：")
    if not 12 <= len(first) <= 1024:
        raise SystemExit("密码应为 12 至 1024 个字符。")
    if first != getpass("再次输入："):
        raise SystemExit("两次密码不一致。")
    print(f'password_hash = "{hash_password(first)}"')


if __name__ == "__main__":
    main()
