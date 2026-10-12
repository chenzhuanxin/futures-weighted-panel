# -*- coding: utf-8 -*-
"""
统一品种表 —— 曲合网（金投网）与天勤（TqSdk）双数据源共用
============================================================

字段说明：
    key    统一主键（写法与天勤一致：交易所.品种），前端与服务端以此为唯一标识
    name   中文品种名
    exch   交易所
    quhe   曲合网（金投网）加权合约代码；None = 曲合网无此品种
    tq     天勤指数合约代码 KQ.i@交易所.品种；None = 天勤无此品种

两个数据源的「加权合约」口径
    · 曲合网：金投网直接提供「XX加权」合约（持仓量加权）
    · 天勤  ：官方「指数合约」KQ.i@EXCH.PRODUCT，按持仓量加权计算

品种来源
    · 曲合网侧：全量遍历 m.quheqihuo.com/quote/hq-<id>.html（hq-1 ~ hq-1500），
                取 symbolName 含「加权」的页面（共 88 个）
                —— 老品种在 hq-983~1053（商品）/ hq-1101~1108（金融），
                   2024 年后上市的新品种落在 hq-1136 / 1163 / 1167 / 1168 /
                   1174 / 1176 / 1185 / 1247 / 1248，容易漏采
    · 天勤侧  ：api.query_quotes(ins_class="FUTURE") 反推产品清单后构造（实测 87 个产品全部有效）

并集共 88 个品种：曲合 88 个（★ 已全量对齐）；天勤 87 个。
仅曲合有「晚籼稻 LR」（郑商所已停止挂牌，天勤无此产品，且曲合侧也无行情数据）。

⚠ 曲合网有 6 个「僵尸品种」页面存在但接口无任何行情数据（郑商所早籼稻/粳稻/普麦/
  强麦/晚籼稻 + 动力煤），因此曲合实际有行情的品种数为 82，天勤为 87。
"""

# (key, 中文名, 交易所, 曲合代码, 天勤指数合约)
_ROWS = [
    # ------------------------------ 上期所 SHFE ------------------------------ #
    ("SHFE.cu", "沪铜",     "上期所", "JO_336254", "KQ.i@SHFE.cu"),
    ("SHFE.al", "沪铝",     "上期所", "JO_336255", "KQ.i@SHFE.al"),
    ("SHFE.zn", "沪锌",     "上期所", "JO_336256", "KQ.i@SHFE.zn"),
    ("SHFE.pb", "沪铅",     "上期所", "JO_336257", "KQ.i@SHFE.pb"),
    ("SHFE.ni", "沪镍",     "上期所", "JO_336253", "KQ.i@SHFE.ni"),
    ("SHFE.sn", "沪锡",     "上期所", "JO_336258", "KQ.i@SHFE.sn"),
    ("SHFE.au", "沪金",     "上期所", "JO_336267", "KQ.i@SHFE.au"),
    ("SHFE.ag", "沪银",     "上期所", "JO_336268", "KQ.i@SHFE.ag"),
    ("SHFE.rb", "螺纹钢",   "上期所", "JO_336261", "KQ.i@SHFE.rb"),
    ("SHFE.wr", "线材",     "上期所", "JO_336269", "KQ.i@SHFE.wr"),
    ("SHFE.hc", "热卷",     "上期所", "JO_336262", "KQ.i@SHFE.hc"),
    ("SHFE.ss", "不锈钢",   "上期所", "JO_336259", "KQ.i@SHFE.ss"),
    ("SHFE.fu", "燃料油",   "上期所", "JO_336263", "KQ.i@SHFE.fu"),
    ("SHFE.bu", "沥青",     "上期所", "JO_336264", "KQ.i@SHFE.bu"),
    ("SHFE.ru", "橡胶",     "上期所", "JO_336265", "KQ.i@SHFE.ru"),
    ("SHFE.sp", "纸浆",     "上期所", "JO_336266", "KQ.i@SHFE.sp"),
    ("SHFE.ao", "氧化铝",   "上期所", "JO_336260", "KQ.i@SHFE.ao"),
    ("SHFE.br", "合成橡胶", "上期所", "JO_336319", "KQ.i@SHFE.br"),
    ("SHFE.ad", "铝合金",   "上期所", "JO_346083", "KQ.i@SHFE.ad"),
    ("SHFE.op", "胶版纸",   "上期所", "JO_347513", "KQ.i@SHFE.op"),
    # --------------------------- 上期能源 INE ------------------------------ #
    ("INE.sc", "原油",      "上期能源", "JO_336270", "KQ.i@INE.sc"),
    ("INE.lu", "低硫燃油",  "上期能源", "JO_336271", "KQ.i@INE.lu"),
    ("INE.nr", "20号胶",    "上期能源", "JO_336272", "KQ.i@INE.nr"),
    ("INE.bc", "国际铜",    "上期能源", "JO_336273", "KQ.i@INE.bc"),
    ("INE.ec", "集运欧线",  "上期能源", "JO_336328", "KQ.i@INE.ec"),
    # ------------------------------ 大商所 DCE ------------------------------ #
    ("DCE.a",  "豆一",      "大商所", "JO_336280", "KQ.i@DCE.a"),
    ("DCE.b",  "豆二",      "大商所", "JO_336281", "KQ.i@DCE.b"),
    ("DCE.m",  "豆粕",      "大商所", "JO_336278", "KQ.i@DCE.m"),
    ("DCE.y",  "豆油",      "大商所", "JO_336279", "KQ.i@DCE.y"),
    ("DCE.p",  "棕榈油",    "大商所", "JO_336282", "KQ.i@DCE.p"),
    ("DCE.c",  "玉米",      "大商所", "JO_336283", "KQ.i@DCE.c"),
    ("DCE.cs", "淀粉",      "大商所", "JO_336284", "KQ.i@DCE.cs"),
    ("DCE.jd", "鸡蛋",      "大商所", "JO_336286", "KQ.i@DCE.jd"),
    ("DCE.rr", "粳米",      "大商所", "JO_336285", "KQ.i@DCE.rr"),
    ("DCE.l",  "塑料",      "大商所", "JO_336290", "KQ.i@DCE.l"),
    ("DCE.v",  "PVC",       "大商所", "JO_336291", "KQ.i@DCE.v"),
    ("DCE.pp", "聚丙烯",    "大商所", "JO_336293", "KQ.i@DCE.pp"),
    ("DCE.eg", "乙二醇",    "大商所", "JO_336292", "KQ.i@DCE.eg"),
    ("DCE.j",  "焦炭",      "大商所", "JO_336276", "KQ.i@DCE.j"),
    ("DCE.jm", "焦煤",      "大商所", "JO_336277", "KQ.i@DCE.jm"),
    ("DCE.i",  "铁矿石",    "大商所", "JO_336275", "KQ.i@DCE.i"),
    ("DCE.fb", "纤维板",    "大商所", "JO_336289", "KQ.i@DCE.fb"),
    ("DCE.bb", "胶合板",    "大商所", "JO_336288", "KQ.i@DCE.bb"),
    ("DCE.lh", "生猪",      "大商所", "JO_336287", "KQ.i@DCE.lh"),
    ("DCE.eb", "苯乙烯",    "大商所", "JO_336294", "KQ.i@DCE.eb"),
    ("DCE.pg", "液化气",    "大商所", "JO_336295", "KQ.i@DCE.pg"),
    ("DCE.bz", "纯苯",      "大商所", "JO_347013", "KQ.i@DCE.bz"),
    ("DCE.lg", "原木",      "大商所", "JO_346547", "KQ.i@DCE.lg"),
    # ------------------------------ 郑商所 CZCE ----------------------------- #
    ("CZCE.SR", "白糖",     "郑商所", "JO_336314", "KQ.i@CZCE.SR"),
    ("CZCE.CF", "棉花",     "郑商所", "JO_336297", "KQ.i@CZCE.CF"),
    ("CZCE.CY", "棉纱",     "郑商所", "JO_336320", "KQ.i@CZCE.CY"),
    ("CZCE.TA", "PTA",      "郑商所", "JO_336315", "KQ.i@CZCE.TA"),
    ("CZCE.OI", "菜籽油",   "郑商所", "JO_336306", "KQ.i@CZCE.OI"),
    ("CZCE.RM", "菜籽粕",   "郑商所", "JO_336310", "KQ.i@CZCE.RM"),
    ("CZCE.RS", "油菜籽",   "郑商所", "JO_336309", "KQ.i@CZCE.RS"),
    ("CZCE.MA", "甲醇",     "郑商所", "JO_336305", "KQ.i@CZCE.MA"),
    ("CZCE.FG", "玻璃",     "郑商所", "JO_336299", "KQ.i@CZCE.FG"),
    ("CZCE.SA", "纯碱",     "郑商所", "JO_336311", "KQ.i@CZCE.SA"),
    ("CZCE.ZC", "动力煤",   "郑商所", "JO_336317", "KQ.i@CZCE.ZC"),
    ("CZCE.SF", "硅铁",     "郑商所", "JO_336312", "KQ.i@CZCE.SF"),
    ("CZCE.SM", "锰硅",     "郑商所", "JO_336313", "KQ.i@CZCE.SM"),
    ("CZCE.AP", "苹果",     "郑商所", "JO_336296", "KQ.i@CZCE.AP"),
    ("CZCE.CJ", "红枣",     "郑商所", "JO_336298", "KQ.i@CZCE.CJ"),
    ("CZCE.UR", "尿素",     "郑商所", "JO_336316", "KQ.i@CZCE.UR"),
    ("CZCE.PF", "短纤",     "郑商所", "JO_336307", "KQ.i@CZCE.PF"),
    ("CZCE.PK", "花生",     "郑商所", "JO_336308", "KQ.i@CZCE.PK"),
    ("CZCE.SH", "烧碱",     "郑商所", "JO_336624", "KQ.i@CZCE.SH"),
    ("CZCE.PX", "对二甲苯", "郑商所", "JO_336638", "KQ.i@CZCE.PX"),
    ("CZCE.WH", "强麦",     "郑商所", "JO_336304", "KQ.i@CZCE.WH"),
    ("CZCE.PM", "普麦",     "郑商所", "JO_336303", "KQ.i@CZCE.PM"),
    ("CZCE.RI", "早籼稻",   "郑商所", "JO_336301", "KQ.i@CZCE.RI"),
    ("CZCE.LR", "晚籼稻",   "郑商所", "JO_336302", None),
    ("CZCE.JR", "粳稻",     "郑商所", "JO_336300", "KQ.i@CZCE.JR"),
    ("CZCE.PL", "丙烯",     "郑商所", "JO_347244", "KQ.i@CZCE.PL"),
    ("CZCE.PR", "瓶片",     "郑商所", "JO_343081", "KQ.i@CZCE.PR"),
    # ------------------------------ 广期所 GFEX ----------------------------- #
    ("GFEX.si", "工业硅",   "广期所", "JO_336274", "KQ.i@GFEX.si"),
    ("GFEX.lc", "碳酸锂",   "广期所", "JO_336318", "KQ.i@GFEX.lc"),
    ("GFEX.ps", "多晶硅",   "广期所", "JO_346548", "KQ.i@GFEX.ps"),
    ("GFEX.pd", "钯",       "广期所", "JO_348637", "KQ.i@GFEX.pd"),
    ("GFEX.pt", "铂",       "广期所", "JO_348623", "KQ.i@GFEX.pt"),
    # ------------------------------ 中金所 CFFEX ---------------------------- #
    ("CFFEX.IF", "沪深300",  "中金所", "JO_338503", "KQ.i@CFFEX.IF"),
    ("CFFEX.IC", "中证500",  "中金所", "JO_338531", "KQ.i@CFFEX.IC"),
    ("CFFEX.IM", "中证1000", "中金所", "JO_338545", "KQ.i@CFFEX.IM"),
    ("CFFEX.IH", "上证50",   "中金所", "JO_338517", "KQ.i@CFFEX.IH"),
    ("CFFEX.T",  "10年国债", "中金所", "JO_338733", "KQ.i@CFFEX.T"),
    ("CFFEX.TF", "5年国债",  "中金所", "JO_338577", "KQ.i@CFFEX.TF"),
    ("CFFEX.TS", "2年国债",  "中金所", "JO_338561", "KQ.i@CFFEX.TS"),
    ("CFFEX.TL", "30年国债", "中金所", "JO_338603", "KQ.i@CFFEX.TL"),
]

VARIETIES = [
    {"key": k, "name": n, "exch": e, "quhe": q, "tq": t}
    for (k, n, e, q, t) in _ROWS
]

KEYS      = [v["key"] for v in VARIETIES]
BY_KEY    = {v["key"]: v for v in VARIETIES}
NAME      = {v["key"]: v["name"] for v in VARIETIES}
EXCH      = {v["key"]: v["exch"] for v in VARIETIES}

# 曲合网代码 <-> 统一 key
QUHE_TO_KEY = {v["quhe"]: v["key"] for v in VARIETIES if v["quhe"]}
KEY_TO_QUHE = {v["key"]: v["quhe"] for v in VARIETIES if v["quhe"]}
QUHE_CODES  = [v["quhe"] for v in VARIETIES if v["quhe"]]

# 天勤代码 <-> 统一 key
TQ_TO_KEY = {v["tq"]: v["key"] for v in VARIETIES if v["tq"]}
KEY_TO_TQ = {v["key"]: v["tq"] for v in VARIETIES if v["tq"]}
TQ_CODES  = [v["tq"] for v in VARIETIES if v["tq"]]


def keys_of(source: str) -> list:
    """返回某数据源可用的全部统一 key。source ∈ {quhe, tianqin}"""
    if source == "quhe":
        return [v["key"] for v in VARIETIES if v["quhe"]]
    if source == "tianqin":
        return [v["key"] for v in VARIETIES if v["tq"]]
    return list(KEYS)


if __name__ == "__main__":
    q = len([v for v in VARIETIES if v["quhe"]])
    t = len([v for v in VARIETIES if v["tq"]])
    print("品种总数 %d  |  曲合可用 %d  |  天勤可用 %d" % (len(VARIETIES), q, t))
    only_tq = [v for v in VARIETIES if not v["quhe"]]
    only_qh = [v for v in VARIETIES if not v["tq"]]
    print("仅天勤有：", "、".join(v["name"] for v in only_tq))
    print("仅曲合有：", "、".join(v["name"] for v in only_qh))
    for v in VARIETIES[:3] + VARIETIES[-3:]:
        print("  %-10s %-8s %-8s quhe=%-11s tq=%s"
              % (v["key"], v["name"], v["exch"], v["quhe"] or "-", v["tq"] or "-"))
