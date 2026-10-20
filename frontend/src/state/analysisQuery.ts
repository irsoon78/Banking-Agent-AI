export const SAMPLE_NOTICE = "模拟消费分析样本，不参与当前账户余额核算。";

export function analysisQuery(period: string, compare = false, throughDay = "") {
  if (!period.trim()) throw new Error("请选择月份或日期范围。");
  if (compare && (period.includes("/") || period === "recent"))
    throw new Error("相邻月份比较需要选择月份。");
  const params = new URLSearchParams({ period, source: "synthetic_demo", mode: compare ? "compare" : "summary" });
  if (throughDay) {
    if (!compare || !/^(?:[1-9]|[12][0-9]|3[01])$/.test(throughDay)) throw new Error("比较天数须为1到31。");
    params.set("through_day", throughDay);
  }
  return params;
}

export function recordsQuery(period: string, offset = 0, category?: string, merchant?: string, recordId?: string) {
  const params = new URLSearchParams({ period, source: "synthetic_demo", limit: "10", offset: String(offset) });
  if (category) params.set("category", category);
  if (merchant) params.set("merchant_id", merchant);
  if (recordId) params.set("record_id", recordId);
  return params;
}

// 只用于图形比例；金额及占比本身均来自服务端，浏览器不重新计算统计。
export function barPercent(value: string | null) {
  if (value == null || !/^\d+(?:\.\d+)?$/.test(value)) return 0;
  return Math.min(100, Math.max(0, Number(value)));
}

export function displayAmount(value: string | null | undefined) {
  if (value == null || !/^-?\d+\.\d{2}$/.test(value)) return "—";
  const [whole, fraction] = value.split(".");
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction}`;
}
