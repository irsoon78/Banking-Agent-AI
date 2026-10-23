// 普通读取复用在途请求；交易后的强制刷新启动新读取，并禁止旧结果覆盖新结果。
export class LatestRead {
  private generation = 0;
  private pending: Promise<void> | null = null;

  invalidate(): void {
    ++this.generation;
    this.pending = null;
  }

  run(
    work: (isLatest: () => boolean) => Promise<void>,
    force = false,
  ): Promise<void> {
    if (this.pending && !force) return this.pending;
    const generation = ++this.generation;
    const result = work(() => generation === this.generation).finally(() => {
      if (generation === this.generation) this.pending = null;
    });
    this.pending = result;
    return result;
  }
}
