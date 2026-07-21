# NotionHub Runner

这个仓库由 NotionHub 自动创建，用于运行已配置同步服务的 GitHub Actions。

- 仓库保持 public，避免消耗私有仓库 Action 免费额度。
- 敏感信息只写入 Repository Secrets，不会出现在仓库文件中。
- 仅为已激活且配置完整的服务生成 workflow。
- 支持媒体的服务会在同一次 workflow 中先同步数据，再下载上传图片和大文件。
- workflow 由 NotionHub 自动更新，手动修改可能会在下次同步时被覆盖。
