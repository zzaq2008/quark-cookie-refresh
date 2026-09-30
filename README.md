# 夸克 Cookie 自动刷新（OpenList）

每天凌晨自动获取夸克网盘最新 cookie，写进 OpenList 里**所有**夸克驱动的存储，
不用再手动复制粘贴。掉登录时脚本会明确报错，扫一次码即可恢复。

> 顺带一提：OpenList 官方的「夸克TV」驱动自带扫码登录 + refresh token 自动续期，
> 是零维护的官方方案（但只支持访问/下载）。本项目适合仍想用传统 Cookie 版
> 夸克驱动、或需要完整读写功能的场景。

## 文件说明

| 文件 | 说明 |
|---|---|
| `docker-compose-allinone.yml` | 一键部署文件（推荐）：脚本以 base64 内嵌，容器启动后自解压，开箱即用 |
| `refresh_quark_cookie.py` | 刷新主脚本：Playwright 取全量 cookie（含 HttpOnly）→ 调 OpenList API 更新 |
| `quark_login_qr.py` | 首次扫码登录脚本（容器内自动调用，一般不用手动跑） |
| `quark-refresh.cron` | 定时任务定义（每天凌晨 3 点，北京时间） |

## 部署（Docker Compose 一键版）

1. **先改两个地方**（打开 `docker-compose-allinone.yml`）：
   - `OPENLIST_PASS`：改成你的 OpenList **管理员密码**（保留引号）
   - `volumes` 下三行的**冒号左侧**：改成你自己的宿主机路径（冒号右侧不要动），例如：
     ```yaml
     - /vol1/docker/quark-refresh/profile:/root/.quark-profile
     ```
2. 在 NAS/服务器的 Docker Compose 里新建项目，粘贴本文件，启动。
   - 需要 `network_mode: host`（文件里已写好），让 `127.0.0.1` 能直连本机 OpenList；
     若 OpenList 不在本机，把 `OPENLIST_URL` 改成实际地址。
3. 首次启动会自动装依赖（约 300MB，走国内镜像，几分钟），然后尝试刷新。
4. 若日志提示扫码：去宿主机 `logs` 目录打开 `qr.png`，用夸克 App 扫码确认。
   登录态会持久化到 `profile` 卷，之后重建容器不用再扫。

## 看到这些日志就是成功了

```
cookie 字段：__pus,__puus,__uid,...
取到最新 cookie，长度 1762
找到 1 个夸克存储：[('/kk', 'Quark')]
OpenList 夸克存储 cookie 已更新（id=2 mount=/kk driver=Quark）
完成
```

之后每天凌晨 3 点（北京时间）自动跑一次。脚本按**驱动类型**识别夸克存储
（驱动名含 `quark` 的都会更新），以后新增夸克存储不用改配置。

## 常见问题

- **OpenList 报 `Invalid username or password`**：`OPENLIST_PASS` 跟实际管理员密码对不上。
  若忘记密码：给 OpenList 容器加环境变量 `OPENLIST_ADMIN_PASSWORD=新密码` 后重启即可重设。
- **报 `require login [guest]`**：cookie 缺关键字段。确认日志里 `cookie 字段` 一行包含
  `__pus`；若没有，说明夸克登录态有问题，按下面重扫。
- **夸克掉登录**（日志明确提示）：进容器终端跑 `python3 /opt/quark-refresh/quark_login_qr.py`，
  按提示扫码即可，`profile` 卷保留，配置不用动。
- **退出码**：`0` 成功；`2` 夸克掉登录；`3` OpenList 侧问题（看日志具体报错）。

## 说明

- 密码只存在你自己机器的环境变量里，不经过任何第三方。
- 夸克网页接口随时可能变化，若某天失效，把报错贴到 Issues，我来修。
