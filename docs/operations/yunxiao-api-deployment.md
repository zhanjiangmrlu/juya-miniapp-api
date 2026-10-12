# juya-miniapp-api 云效测试环境手动配置

本配置参考 juya-admin-api 的镜像制品发布方式，不使用 ACR。流水线只允许 `test`
发布，运行真实业务模式，复用管理后端测试环境已有的 MySQL、Redis 和容器网络。
数据库迁移由 admin-api 负责；本流水线在测试阶段对临时数据库运行管理端迁移，
ECS 发布阶段不执行迁移、不新建数据库或数据卷、不重启管理端。

## 1. 修改代码源和触发器

| 项目 | 配置 |
|---|---|
| 流水线名称 | `juya-miniapp-api-test` |
| 主代码源 | `zhanjiangmrlu/juya-miniapp-api` |
| 主代码源分支、触发分支 | `test` |
| 第二代码源 | `zhanjiangmrlu/juya-admin-api`，分支 `test` |
| 工作目录 | miniapp-api 仓库根目录 |
| 第二代码源默认检出路径 | miniapp-api 根目录的相邻 `../juya-admin-api` |
| 第二代码源路径变量 | 若实际路径不同，给测试任务设置 `JUYA_CI_ADMIN_SOURCE` 为真实绝对路径 |
| 主机组 | 仅包含目标测试 ECS，可复用管理后端测试主机组 |

主代码源绑定 `CI_COMMIT_REF_NAME` 和 `CI_COMMIT_SHA`，不能使用第二代码源的提交号
作为 miniapp 镜像标签。第二代码源只用于隔离测试，不随 miniapp 制品部署管理端。
测试日志会记录实际管理端迁移源码的提交号。`.aliyun-ci.yml` 提供编排参考；
已有流水线通过控制台配置代码源、服务连接、Docker Daemon 和主机组。

## 2. 隔离测试任务

构建环境可以沿用管理端当前 Alibaba Cloud Linux 3；关闭 Docker Daemon。
该任务必须成功后才能进入镜像构建和部署阶段；不要配置“忽略失败”。

```sh
set -eu
test "${CI_COMMIT_REF_NAME}" = test
sh deploy/ci-verify.sh
```

脚本安装 Python 3.13、uv 0.12.19、MySQL 8.4.6、Redis 7.4.2。
沿用管理端公共集群适配：在任务容器内启动原生 MySQL/Redis，不调用 Docker 创建网络。
服务仅绑定 `127.0.0.1:13306`/`127.0.0.1:16379`，使用本次 UUID 临时库。
脚本清除继承的 `JUYA_*`/`OSS_*` 配置，关闭真实 OSS 测试，不读取 ECS 数据库配置。
管理端与小程序后端使用各自锁定依赖的独立虚拟环境，随后检查 Ruff、格式、mypy、
单元/契约/数据库集成/跨服务 HTTP 测试及覆盖率下限 80%。

公共集群额外排除下面四个要求本地 Docker 标签及资源归属证明的测试文件，
不会修改它们的资源保护逻辑，也不能把该任务描述为“全部测试通过”：

- `tests/integration/test_v15_private_socket_kit.py`
- `tests/integration/test_v15_socket_confirmation.py`
- `tests/integration/test_v15_legacy_recording_socket.py`
- `tests/integration/test_v15_content_namespace_sql.py`

这些用例可在具备本地 Docker 的环境通过现有命令运行：

```sh
uv run python scripts/test-v15-isolated.py -q --cov=juya_miniapp_api --cov-fail-under=80
```

## 3. 镜像构建任务

与隔离测试分成独立任务，开启 Docker Daemon；前置测试失败必须阻止构建。

```sh
set -eu
test "${CI_COMMIT_REF_NAME}" = test
sh deploy/build-artifact.sh "${CI_COMMIT_REF_NAME}" "${CI_COMMIT_SHA}" artifact
```

镜像标签为 `juya-miniapp-api-test:<主代码源40位提交SHA>`，平台为 `linux/amd64`。
制品包含压缩 Docker 镜像、镜像标签、配置摘要、SHA256 校验清单和 `deploy` 配置。
镜像入口已经在 Dockerfile 中配置，无须在流水线额外写 uvicorn 启动命令。

## 4. 构建物上传

| 字段 | 值 |
|---|---|
| 上传方式 | 归档至云效公共存储空间，供后续任务拉取并部署 |
| 制品名称 | `juya_miniapp_api_test` |
| 打包路径 | `artifact/` |

## 5. 主机部署任务

| 字段 | 值 |
|---|---|
| 制品 | 选择上一步 miniapp 任务产出的制品 |
| 部署时下载制品 | 勾选 |
| 下载路径 | `/home/admin/app/juya-miniapp-api-test.tgz` |
| 执行用户 | `root`，与管理后端现有部署方式一致 |
| 部署目录 | 脚本固定 `/opt/juya/juya-miniapp-api` |
| Compose 项目 | `juya-miniapp-test` |
| 主机 API 端口 | `127.0.0.1:8001`，容器内部 `8000` |

部署脚本填写：

```sh
set -eu
test "${CI_COMMIT_REF_NAME}" = test
staging="$(mktemp -d /tmp/juya-miniapp-api-test.XXXXXX)"
trap 'rm -rf "$staging"' EXIT
tar xzf /home/admin/app/juya-miniapp-api-test.tgz -C "$staging"
bundle="$staging/artifact"
if [ ! -d "$bundle" ]; then bundle="$staging"; fi
sh "$bundle/deploy/ecs-artifact-deploy.sh" "${CI_COMMIT_REF_NAME}" "$bundle"
```

部署会校验制品 SHA256、镜像平台和配置摘要，取得发布锁后运行配置与依赖预检，
再停止旧 Beat/Worker/API。启动 API、检查就绪后启动唯一 Worker 和 Beat。
首次发布失败会停止本次应用；升级失败恢复上一版本应用。恢复不降级数据库，
也不撤销人工编辑的环境变量。不会执行 `docker compose down` 或删除共享资源。

## 6. ECS 首次准备

沿用现有服务器的 Docker Compose、python3、curl、flock。先确认管理端测试部署正常，
共享库已经由 admin-api 迁移到当前 `test` 版本，miniapp 当前至少要求 schema_version 19。
外部网络默认 `juya-admin-small_default`；如现有名称不同，以实际网络为准。

```sh
docker network inspect juya-admin-small_default >/dev/null
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready
free -h
docker stats --no-stream
```

原管理端 2 GiB 配置的各容器内存上限之和约 1696 MiB，新增 API/Worker/Beat 上限
为 256/192/96 MiB，总和约 2240 MiB。上限不是预分配，但同机部署需按实际可用内存、
峰值和 OOM 情况评估，不能仅依据构建成功判断容量足够。

在服务器创建两个独立配置文件，按仓库模板填写，不要覆盖管理端 `/etc/juya/compose.env`：

- `/etc/juya/miniapp-compose.env`：参考 `deploy/.env.ecs-test.example`
- `/etc/juya/miniapp-api.env`：参考 `deploy/.env.runtime-test.example`

`miniapp-compose.env` 填写：

```dotenv
JUYA_MINIAPP_API_IMAGE=juya-miniapp-api-test:replace-with-40-character-git-sha
JUYA_RUNTIME_ENV_FILE=/etc/juya/miniapp-api.env
JUYA_MINIAPP_API_PORT=8001
JUYA_SHARED_NETWORK=juya-admin-small_default
```

发布时脚本会覆盖镜像变量为当前制品中的不可变标签；模板占位标签不用手工逐次更新。
`miniapp-api.env` 必须提供现有测试数据库应用账号密码、共享 HMAC、独立 JWT、
字段加密与查找密钥、真实微信 AppID/AppSecret、`juya-test` 桶和 OSS RAM 角色。
默认使用 ECS RAM Role；若服务器使用环境凭证，把 `JUYA_OSS_CREDENTIALS_MODE` 改为
`environment` 并在该文件配置 `JUYA_OSS_ACCESS_KEY_ID`/`JUYA_OSS_ACCESS_KEY_SECRET`。
不要把已有加密/查找密钥当作每次发布的随机变量重新生成。

```sh
chmod 600 /etc/juya/miniapp-compose.env /etc/juya/miniapp-api.env
```

还要把现有管理端运行配置 `/etc/juya/admin-api.env` 中的回调地址设置为：

```dotenv
JUYA_MINIAPP_API_BASE_URL=http://miniapp-api:8000
```

容器内的 `127.0.0.1:8001` 指向管理容器自身，不能用于访问另一个容器。
上述修改需通过管理端现有部署流程重新创建其 API/Worker/Beat 后生效。
双方 API/Worker/Beat 的 `JUYA_INTERNAL_HMAC_SECRET` 值必须一致。

## 7. Nginx 测试域名

本次脚本不自动修改 Nginx。miniapp 部署成功后，在已有 `test-api.juyayingyu.com`
HTTPS 站点删除用于占位的 `return 503`，将代理目标改为 `127.0.0.1:8001`。
保留现有证书及 HTTP 跳转 HTTPS 配置。对应 location 为：

```nginx
location / {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

若测试 H5 使用同源 `/api/`，也在 `test-h5` 站点代理该路径到 `8001`。
真实业务模式未设置跨域 CORS，不能直接假定 H5 跨域 API 已可用。
微信小程序使用 `https://test-api.juyayingyu.com` 时另需配置合法请求域名并重新构建前端。

```sh
nginx -t
systemctl reload nginx
curl --fail --silent --show-error http://127.0.0.1:8001/health/ready
curl --fail --silent --show-error https://test-api.juyayingyu.com/health/ready
curl --silent --show-error -i https://test-api.juyayingyu.com/api/v1/me
```

最后一个匿名请求应返回 `401` 和 `ACCESS_TOKEN_REQUIRED`，用于确认真实业务路由存在。
这些检查不代替真实微信登录、OSS 上传、注销跨服务回调与真机验收。

云效主机部署配置方式见 [阿里云官方文档](https://help.aliyun.com/zh/yunxiao/user-guide/host-deployment-1)。

## 8. 本次本地验证（2026-10-12）

- Windows 完整 UUID 隔离运行：`scripts/test-v15-isolated.py -q`，399 passed、1 skipped；
  临时资源已清理，共享服务未重启。
- 在本地启动云效 Alibaba Cloud Linux 3 构建镜像执行 `deploy/ci-verify.sh`：
  395 passed、1 skipped，覆盖率 83.50%；四个 Docker 归属用例按上述边界排除。
- Ruff、格式、mypy、Shell 语法检查、Compose 配置检查通过。
- Docker `linux/amd64` 镜像构建通过，容器真实启动后 `/health/live` 返回 200，
  匿名 `/api/v1/me` 返回 401 `ACCESS_TOKEN_REQUIRED`。
- 15 个部署/打包用例覆盖首次发布、升级失败恢复、破损制品、镜像身份不匹配、
  配置预检失败、Worker 未正常退出和同一 SHA 的制品冲突。

以上是本地验证，不代表云效服务连接、目标 ECS 网络、RAM 角色、TLS 站点或真实微信
登录已经验收。实际云效发布与公网检查由手动配置后运行流水线完成。
