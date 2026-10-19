import { test } from "node:test";
import assert from "node:assert/strict";
import { LatestRead } from "./latestRead.ts";

test("post-action refresh starts a fresh read and stale pre-action results cannot publish", async () => {
  const read = new LatestRead();
  let resolveOld!: () => void;
  let resolveNew!: () => void;
  const published: string[] = [];
  const old = read.run(async (latest) => {
    await new Promise<void>((r) => {
      resolveOld = r;
    });
    if (latest()) published.push("old");
  });
  assert.equal(
    read.run(async () => {
      throw new Error("ordinary read should be deduplicated");
    }),
    old,
  );
  const fresh = read.run(async (latest) => {
    await new Promise<void>((r) => {
      resolveNew = r;
    });
    if (latest()) published.push("new");
  }, true);
  assert.notEqual(old, fresh);
  resolveNew();
  await fresh;
  resolveOld();
  await old;
  assert.deepEqual(published, ["new"]);
});
