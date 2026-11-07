"""外观与工艺特征：用家具设计词库从标题里提取风格、材质、造型、工艺、颜色、功能卖点，
再和全部家具基线对比，找出爆火/潜力商品明显偏多的设计特征。

词库是“英文标题写法 → 中文特征名”。一个商品可以命中同一维度的多个特征。
想补充词条直接改下面的 LEXICON 即可（正则，按单词边界匹配，不区分大小写）。
写法为 None 的只给主图识别用（标题里很少这样写，例如“方正直线”“细腿/高脚”）。
词库也是主图识别的词表（vision.py），改了词库，之后识别的图片会按新词表重看一遍。
2026-10 按监控范围（板材 / 实木 / 铁木的柜类、桌类、床架、置物架）重写：补上开放格、错落层板、壁挂、带轮、
可伸缩、升降台面、线缆管理、深咖 / 橡木色等，去掉沙发床垫用的云朵造型、翼背、深座、可躺、凉感、软包、记忆棉等。
"""
from __future__ import annotations

import re
from collections import Counter

# 维度顺序即报告里的展示顺序：外观和工艺在前，功能卖点在后
LEXICON: dict[str, list[tuple[str, str]]] = {
    "风格": [
        ("中古风", r"mid[- ]?century|\bmcm\b"),
        ("农舍风", r"farmhouse"),
        ("乡村复古", r"rustic"),
        ("工业风", r"industrial"),
        ("波西米亚", r"boho|bohemian"),
        ("日式侘寂", r"japandi|japanese|wabi[- ]?sabi"),
        ("北欧风", r"scandinavian|nordic"),
        ("极简", r"minimalis[tm]"),
        ("奶油风", r"cream style|french cream"),
        # luxury / classic 多是卖点话（Luxury Vanity、classic freestanding shelf），不说明风格，不算
        ("轻奢", r"\bglam\b"),
        ("海岸风", r"coastal"),
        ("复古", r"vintage|retro|antique"),
        ("古典欧式", r"victorian|traditional|french country|baroque"),
        ("夏克/传教士风", r"\bshaker\b|\bmission\b"),
        ("现代", r"\bmodern\b|contemporary"),
    ],
    "材质": [
        ("实木", r"solid wood|real wood|solid (?:oak|pine|acacia|walnut|rubberwood|mango)"),
        ("相思木/橡胶木/松木", r"acacia|rubberwood|rubber wood|\bpine\b|mango wood|paulownia"),
        ("竹", r"bamboo"),
        ("人造板", r"engineered wood|\bmdf\b|particle ?board|manufactured wood"),
        ("金属", r"\bmetal\b|\bsteel\b|\biron\b|aluminum"),
        ("藤/编织", r"rattan|wicker|\bcane\b|seagrass|woven"),
        ("皮革/仿皮", r"leather|\bpu\b|leatherette"),
        ("大理石纹", r"marble"),
        # slate 多是颜色（slate gray），stone 多是底座配重：只认写明是岩板 / 石材台面的
        ("岩板", r"sintered stone|\bstone (?:top|tabletop|base)|faux stone"),
        ("玻璃", r"\bglass\b"),
        ("亚克力", r"acrylic"),
    ],
    "造型": [
        ("开放格", r"open (?:shel(?:f|ves|ving)|storage|cubb(?:y|ies)|compartments?)|cubbies|\bcube (?:storage|shelf|bookcase|organizer)"),
        # ladder 只认梯形书架：上下铺、阁楼床的爬梯不算
        ("错落/阶梯层板", r"staggered|asymmetr|ladder (?:shel(?:f|ves)|bookcase|bookshelf|book shelf)|zig[- ]?zag"),
        # 只认柜架本身高：“40\" Tall Dresser”这种写尺寸的不算，写明 55 英寸以上的才算；vertical 多是竖放（vertical murphy bed）、
        # 竖向凹槽（vertical grooved doors），pc tower 是电脑主机架
        ("高柜/立式", r"\btall (?:bookcase|bookshel(?:f|ves)|book shel(?:f|ves)|cabinet|pantry|cupboard|storage|display|shel(?:f|ves)|"
                      r"wardrobe|armoire|boy|narrow|corner|kitchen|bathroom|accent|linen|utility|garage)|"
                      r"\b(?:5[5-9]|[6-9]\d)(?:\.\d+)?\s*-?\s*(?:\"|''|”|″|in(?:ch(?:es)?)?\.?)\s*-?\s*(?:h\b|tall\b|high\b|height\b)|"
                      r"(?<!pc )(?<!cpu )(?<!computer )\btower\b|highboy"),
        ("窄小省空间", r"\bslim\b|\bnarrow\b|compact|small space|space[- ]saving"),
        ("L 形/U 形", r"\bl[- ]?shaped?\b|\bu[- ]?shaped?\b"),
        # 圆角（round corner legs、rounded corner）不是转角款
        ("转角款", r"(?<!round )(?<!rounded )\bcorner\b(?! (?:legs?|design|guards?|protect))"),
        ("壁挂/悬浮", r"wall[- ]mount(?:ed)?|floating|wall shel(?:f|ves)"),
        # floor standing 只是落地放（相对壁挂），不是矮、贴地
        ("低矮落地", r"low[- ]profile|floor bed|floor-level"),
        ("圆形/椭圆", r"\bround\b|circular|\boval\b"),
        ("弧形曲线", r"curved|\bcurve\b|crescent|half[- ]moon"),
        ("拱形", r"\barch(?:ed)?\b"),
        # combination 多是密码锁（combination lock）
        ("模块化组合", r"modular|stackable"),
        # pedestal 只认桌子的中央底座；U 形钢架（u-shape carbon steel pedestal）、办公桌下的抽屉柜不算
        ("单柱/郁金香底座", r"pedestal (?:base|table|dining|end table|side table|coffee|accent)|"
                            r"(?:round|single|central|center|wood(?:en)?|heavy[- ]duty|sturdy|accent|fluted) pedestal|tulip"),
        ("瀑布式", r"waterfall"),
        # 腿和床型是造型（以前放在工艺里）
        ("锥形/外八腿", r"tapered|splayed|hairpin"),
        ("雪橇床型", r"sled (?:base|legs?)|sleigh"),
        # 不加“方正直线”“框架式”“细腿”这类词：实测主图识别会给八九成商品都打上（方正直线 44/50、对照 32/40），
        # 说明不了什么，还挤掉了“锥形/外八腿”“玻璃门”这类具体特征（每张图每个维度最多 4 个）
    ],
    "工艺": [
        ("凹槽竖纹", r"fluted|reeded|ribbed|tambour|grooved (?:doors?|fronts?|panels?|drawers?)|vertical grooves?"),
        # 床架的支撑条（wood slats support、metal slats）看不见，不算；只认带空隙板条拼的门、床头、凳面、护栏
        ("条栅", r"\bslatted\b(?! (?:bed|base|platform|frame|support|foundation))|"
                 r"\bslat (?:doors?|fronts?|panels?|headboard|design|bench|top)|fence slats"),
        ("藤编/编织", r"rattan|cane|wicker|woven"),
        ("玻璃门", r"glass doors?"),
        ("谷仓门/推拉门", r"barn doors?|sliding doors?"),
        ("百叶门", r"louver(?:ed)?|shutter"),
        ("镜面", r"mirrored"),
        # cut-out 多是拉手开孔，不算
        ("镂空/格栅", r"hollow(?:ed)?[- ]out|hollow carved|lattice|fretwork|openwork"),
        ("雕刻", r"carved|carving"),
        ("自然边/做旧", r"live edge|distressed|reclaimed|weathered"),
        ("双色拼接", r"two[- ]tone|two[- ]colou?r|dual[- ]tone"),
        # greenguard gold 是环保认证
        ("金色金属件", r"(?<!greenguard )\bgold\b|brass"),
        ("高光烤漆", r"high gloss|glossy|lacquer"),
    ],
    "颜色": [
        ("白色", r"\bwhite\b"),
        ("黑色", r"\bblack\b"),
        ("灰色", r"\bgr[ae]y\b"),
        ("灰褐/做旧灰", r"greige|gr[ae]y wash|driftwood|ash gr[ae]y|rustic gr[ae]y"),
        ("奶油/米色", r"\bbeige\b|\bcream\b|ivory|off[- ]white|oatmeal|\bsand\b"),
        ("驼色/焦糖", r"camel|caramel|cognac|\btan\b|taupe|mushroom"),
        ("棕色", r"\bbrown\b"),
        ("深咖色", r"espresso|chocolate|dark brown"),
        ("胡桃色", r"walnut"),
        ("橡木色", r"\boak\b"),
        ("原木色", r"natural|light wood|wood color"),
        ("樱桃木/红棕", r"cherry|mahogany"),
        ("绿色", r"green|sage|olive"),
        ("蓝色", r"\bblue\b|navy"),
        ("粉色", r"\bpink\b|blush"),
    ],
    "功能卖点": [
        ("带储物/抽屉", r"storage|drawers?"),
        # light 单独出现多是颜色（Light Brown、light wood、and Light Gray），不算灯
        ("LED 灯", r"\bled\b|\blights\b|\blighted\b|\blighting\b|night ?light|light[- ]up|"
                   r"(?:with|and|&|\+)\s*(?:a\s+)?light\b(?!\s*(?:brown|gr[ae]y|wood|oak|walnut|blue|green|pink|beige|cherry|"
                   r"natural|rustic|colou?r|tan|teak|white|black))"),
        ("充电/插座", r"charging|outlets?|\busb\b|power strip"),
        # “3 Adjustable Heights”（衣帽架几档高度）不算：只认单数的 adjustable height
        ("电动/可升降", r"sit[- ]stand|standing desk|height[- ]adjustable|adjustable height(?!s)|electric (?:standing )?desk"),
        ("升降台面", r"lift[- ]?top"),
        ("电子壁炉", r"fireplace"),  # 只看标题（不在 VISION_FUNCTIONS 里，改了主图词表要全部重看）
        ("可调层板", r"adjustable (?:shel(?:f|ves)|shelving)"),
        ("可伸缩/加长", r"extendable|expandable|extension (?:table|leaf|leaves)|telescoping"),
        ("带轮可移动", r"wheels?|casters?|rolling|\bmobile\b"),
        ("带镜子", r"\bmirror\b|with mirror"),
        ("线缆管理", r"cable management|cord management|wire management|cable tray|cord hole"),
        ("带挂钩", r"hooks?"),
        # lockable / locking casters 是刹车轮，不是锁
        ("带锁", r"lockable(?! (?:casters?|wheels?))|with locks?\b|locking(?! (?:casters?|wheels?))|combination lock"),
        ("显示器架/键盘托", r"monitor (?:stand|shelf|riser)|keyboard tray"),
        ("配凳子", r"with (?:stool|bench|chair)"),
        # 按压开门只看标题：主图看不出来，实测主图识别把开放格（没有门）也当成“无把手”，给了 30/50 个商品
        ("按压开门/无把手", r"handle[- ]?free|handleless|push[- ]to[- ]open|push open"),
        ("可折叠", r"fold(?:able|ing)|collapsible"),
        # 360 单独出现多是“360° View Mirror”；swivel bar stools 是配的凳子会转
        ("旋转", r"swivel(?! (?:bar )?(?:stools?|chairs?))|rotating|revolving|spinning|lazy susan"),
        ("免安装/易安装", r"no assembly|tool[- ]free|easy assembly|quick assembly|assembly[- ]free"),
        ("人体工学", r"ergonomic"),
        ("防倾倒", r"anti[- ]tip|wall anchor"),
        ("防水/防潮", r"waterproof|water[- ]resistant|moisture[- ]proof"),
    ],
}

DESIGN_DIMENSIONS = ("风格", "材质", "造型", "工艺", "颜色", "功能卖点")  # 主图识别要读的维度
# 主图识别只认看得见的功能（抽屉、灯带、镜子、轮子、挂钩、插座口…）；按压开门、防倾倒、防水、线缆管理、可伸缩这类看不出来的只看标题
VISION_FUNCTIONS = ("带储物/抽屉", "LED 灯", "充电/插座", "带镜子", "带轮可移动", "带挂钩", "升降台面", "电动/可升降",
                    "显示器架/键盘托", "配凳子")
# 主图上的材质只认看得出来的：木纹看不出是实木还是板材（11-02 实测：主图说实木的 57 个，亚马逊 Material 也说实木的只有 6 个），
# 一律写“木质”；岩板和大理石纹的板看着一样，只看标题
VISION_MATERIALS = ("木质", "金属", "玻璃", "大理石纹", "藤/编织", "皮革/仿皮", "亚克力", "竹")

# 主图识别的词表：每个特征一句看图就能核对的定义（含“不算”的情况），写进识别提示词，README 也照这个写。
# 2026-11-07 用户反馈：点“凹槽竖纹”出来的不是凹槽竖纹。抽查旧提示词（只给词表、不给定义）的结果，
# 凹槽竖纹 36 张里只有 13 张真是（把谷仓门 X 形线条、嵌框门框线、木纹、拼板、软包竖向分割、条栅都当成了凹槽），
# 双色拼接 5/12、锥形/外八腿 3/12、高光烤漆 3/12、玻璃门 4/12、条栅 4/12、低矮落地 1/12
DEFINITIONS: dict[str, str] = {
    # 风格：只写一个主风格，这里是判断依据
    "中古风": "锥形外八细腿、圆角或弧形柜体、胡桃木色、凹槽门板这类 1950~60 年代的样子",
    "农舍风": "X 形谷仓门、做旧白 / 灰木纹、黑色铁件、粗木台面",
    "乡村复古": "粗犷原木或做旧木纹为主、质朴，但没有谷仓门这类农舍符号",
    "工业风": "黑色金属管架或角铁框架配木板，裸露的金属结构是主角",
    "波西米亚": "藤编、编织、流苏或彩色图案为主",
    "日式侘寂": "低矮、浅原木、线条极简、自然质感",
    "北欧风": "浅木色配白、圆润简洁、细木腿",
    "极简": "没有任何装饰的平板几何体（无拉手、无线条、无腿型变化）",
    "奶油风": "奶油白 / 米色为主、轮廓圆润柔和",
    "轻奢": "金色金属件、大理石纹、高光或镜面这类显贵的元素是主角",
    "海岸风": "白 / 浅蓝配浅木、百叶、度假感",
    "复古": "有年代感的造型或做旧，但说不上中古风、农舍风",
    "古典欧式": "雕花、弯腿、线脚等繁复的古典装饰",
    "夏克/传教士风": "嵌框平板门（Shaker 门）、直线方腿、实木质朴",
    "现代": "看不出上面任何一种具体风格的普通现代款",
    # 材质（主图）
    "木质": "主体是木头或木纹板（看不出是实木还是板材，一律写木质）",
    "金属": "框架、腿或柜体本身是金属（拉手、铰链、滑轨、升降五金这些小件不算）",
    "玻璃": "有玻璃台面、玻璃门或玻璃层板",
    "大理石纹": "台面或柜面是大理石纹理",
    "藤/编织": "有藤编、编织面板或编织篮",
    "皮革/仿皮": "有皮面或仿皮面（坐垫、软包面）",
    "亚克力": "有透明亚克力件",
    "竹": "竹子做的",
    # 造型
    "开放格": "有不带门的敞开格子或层板，直接看得到里面放的东西",
    "错落/阶梯层板": "层板高低错开，或像梯子一样斜靠、逐层收窄（整齐等高的层板不算）",
    "高柜/立式": "整体很高（到成人胸口或更高、约 1.2 米以上）的柜、架：高柜、高书架、立式收纳塔、衣帽架；"
                 "床头柜、边桌、电视柜、玄关桌这些矮家具，以及床都不算",
    "窄小省空间": "宽度明显窄：窄边桌、窄高柜、沙发后窄桌这类",
    "L 形/U 形": "台面或柜体拐成 L 形或 U 形",
    "转角款": "专门放墙角的款（三角形、扇形或转角拼接）；只是圆角不算",
    "壁挂/悬浮": "挂在墙上，底部离地、看不到腿",
    "低矮落地": "整体很矮（大约不到膝盖高）而且底部直接贴地、看不到腿：落地床、地台床、无腿矮凳柜；"
                "有腿的、普通高度的柜子只是底座贴地的都不算",
    "圆形/椭圆": "台面或柜体的轮廓是圆形或椭圆",
    "弧形曲线": "柜体、台面或框架有明显的弧形 / 曲线轮廓；只是圆角不算，整体是圆形 / 椭圆的写“圆形/椭圆”，镜子、灯的形状不算",
    "拱形": "有拱形门、拱形开口或拱形顶",
    "模块化组合": "由几个可以分开、自由拼搭的单元组成",
    "单柱/郁金香底座": "桌面下只有一根中央立柱或郁金香式底座",
    "瀑布式": "台面在两端直接折下来当侧板",
    "锥形/外八腿": "腿上粗下细，或向外斜着张开；直腿、方腿、金属直管腿不算",
    "雪橇床型": "床头、床尾向外翻卷成雪橇形",
    # 工艺
    "凹槽竖纹": "门板、抽屉面、柜身或底座的木头 / 板材表面整面布满密集、等宽、平行的竖向凹槽或半圆凸条（fluted / reeded）；"
                "布面、灯芯绒的竖条纹，"
                "木纹、拼板缝、门板上一两道装饰线、X 形谷仓门线条、嵌框门的框线、百叶、有空隙的板条、软包竖向分割、长虹玻璃都不算",
    "条栅": "用一根根带空隙的木条拼成的门、护栏、床头或凳面；看不见的床垫支撑条、没有空隙的凹槽都不算",
    "藤编/编织": "门板或抽屉面嵌了藤编、编织面",
    "玻璃门": "柜门是玻璃、隔着门看得到里面（透明或长虹玻璃）；高光门板、镜子、玻璃台面都不算",
    "谷仓门/推拉门": "门板上有 X 形或 Z 形谷仓门斜撑，或者是轨道推拉门",
    "百叶门": "门板由一片片斜的横向百叶组成",
    "镜面": "柜门或柜面本身就是一整块镜子（如柜门是全身镜）；梳妆台的化妆镜不管单面还是三折都不算（那是“带镜子”）",
    "镂空/格栅": "门板、侧板或底座本身做成透空的花纹或格栅（雕花透孔、金属网格、几何镂空框）；"
                 "X 形加固拉杆、护栏、框架之间的空当都不算",
    "雕刻": "有立体雕花或浮雕装饰",
    "自然边/做旧": "保留树皮自然边的台面，或刻意做旧（斑驳、刷白、仿旧木纹）",
    "双色拼接": "木质柜体 / 台面本身用了两种明显不同的颜色（如白柜体配原木色台面）；金属框架或腿和木板的颜色差、拉手、灯光不算",
    "金色金属件": "有金色 / 黄铜色的拉手、脚套、框架或装饰条",
    "高光烤漆": "表面像镜子一样反光的亮面漆；普通哑光白、木纹面不算",
    # 功能（看得见的）
    "带储物/抽屉": "有抽屉、柜门或储物格",
    "LED 灯": "家具上装了灯带或灯泡",
    "充电/插座": "图里看得到插座口、USB 口或无线充电位",
    "带镜子": "带化妆镜或全身镜",
    "带轮可移动": "底下有轮子",
    "带挂钩": "有挂钩",
    "升降台面": "茶几等的台面能向上抬起",
    "电动/可升降": "桌腿是升降立柱、带升降控制器",
    "显示器架/键盘托": "有显示器增高架或键盘抽托",
    "配凳子": "同一套里配了凳子或长凳",
}

# 旧识别结果里词表以外的写法 → 词表里的说法（None = 不是监控范围内的特征，丢掉）；按特征名找维度（LABEL_DIM），放错维度的也归位
ALIASES: dict[str, str | None] = {
    "实木": "木质", "人造板": "木质", "相思木/橡胶木/松木": "木质", "松木": "木质", "胡桃木": "木质", "木皮": "木质",
    "岩板": None, "仿石纹": None, "棉麻布艺": None, "布艺": None, "记忆棉": None, "独立弹簧/混合": None,
    "悬浮": "壁挂/悬浮", "L形": "L 形/U 形", "L 形": "L 形/U 形", "U 形": "L 形/U 形", "阶梯层板": "错落/阶梯层板",
    "深座/超大": None, "翼背/高背": None, "对称式": None,
    "做旧": "自然边/做旧", "拱形门": "拱形", "拱形门板": "拱形", "软包": None, "黑色金属件": None,
    "带储物": "带储物/抽屉",
    "浅木色": "原木色", "木色": "原木色", "原木": "原木色", "米色": "奶油/米色", "米白色": "奶油/米色", "奶油色": "奶油/米色",
    "灰褐色": "灰褐/做旧灰", "深棕色": "深咖色", "咖啡色": "深咖色", "胡桃木色": "胡桃色", "橡木": "橡木色",
}
# 维度 → 主图词表
VISION_VOCAB: dict[str, tuple[str, ...]] = {
    "风格": tuple(label for label, _ in LEXICON["风格"]),
    "材质": VISION_MATERIALS,
    "造型": tuple(label for label, _ in LEXICON["造型"]),
    "工艺": tuple(label for label, _ in LEXICON["工艺"]),
    "颜色": tuple(label for label, _ in LEXICON["颜色"]),
    "功能卖点": VISION_FUNCTIONS,
}
LABEL_DIM: dict[str, str] = {label: dim for dim, labels in VISION_VOCAB.items() for label in labels}
_BY_KEY = {label.replace(" ", ""): label for label in LABEL_DIM}  # “L形/U形”“LED灯”这类少了空格的写法

# 旧提示词（2026-11-07 以前，只给词表不给定义）的识别结果：造型 / 工艺 / 功能 / 材质只有下面这些抽查可靠的照用
# （谷仓门/推拉门 12/12、带镜子 12/12、开放格 11/12；木质、金属看得出），其余的要标题也写了这个特征才算（标题写 fluted 的
# 主图 11/11 真有凹槽；只有主图说有、标题没写的 25 个里只有 2 个是）。颜色、风格只取主色、主风格（主色抽查 23/24 对）。
# 新提示词的结果（vision.PROTOCOL）不受这条限制
LEGACY_GATED = ("材质", "造型", "工艺", "功能卖点")
LEGACY_TRUSTED = frozenset({"开放格", "谷仓门/推拉门", "带镜子", "木质", "金属"})
# 看尺寸的特征照片上判断不了（没有参照物）：新提示词加复核后，“高柜/立式”还是把墨菲床、带高架的书桌、梳妆台算进去（11-07 抽查 6/9），
# 所以不管哪种识别结果，都要标题也写明（写了 55 英寸以上的高度，或 tall cabinet / tall bookcase、tower 这类）才算
TITLE_REQUIRED = frozenset({"高柜/立式"})


def canonical_label(raw: str) -> str | None:
    """一个主图标签在词表里的说法；None = 丢掉。词表以外、也没有别名的原样返回（canonical_tags 再决定留不留）。"""
    label = str(raw or "").strip()
    key = label.replace(" ", "")
    if label in ALIASES or key in ALIASES:
        return ALIASES.get(label, ALIASES.get(key))
    return _BY_KEY.get(key, label) or None


def canonical_tags(tags: dict) -> dict[str, list[str]]:
    """主图标签归位：词表以外的写法按 ALIASES 换成词表里的说法、不在范围内的丢掉，放错维度的（工艺里的“拱形”、造型里的“带轮可移动”）
    挪回它的维度。颜色允许词表以外的“X色”（金色、紫色……：照实归类）；其他维度词表以外的都丢掉（以前会原样变成一个特征）。"""
    out: dict[str, list[str]] = {dim: [] for dim in DESIGN_DIMENSIONS}
    for dim in DESIGN_DIMENSIONS:
        for raw in (tags or {}).get(dim) or []:
            label = canonical_label(raw)
            if not label:
                continue
            home = LABEL_DIM.get(label)
            if home is None and dim == "颜色" and label.endswith("色") and 2 <= len(label) <= 4:
                home = "颜色"
            if home and label not in out[home]:
                out[home].append(label)
    return out
# 报告里对比的外观维度。材质只看“主材质”（亚马逊 Material 属性，见 materials.py），
# 不再用标题词库 / 主图标签另算一套，免得同一件事出现三种口径
APPEARANCE_DIMENSIONS = ("风格", "造型", "工艺", "颜色")
_COMPILED = {dim: [(label, re.compile(pattern, re.I)) for label, pattern in rules if pattern]
             for dim, rules in LEXICON.items()}
# 颜色以主图识别为准：标题里的颜色常是把可选变体都列出来（Walnut/Black/White），不代表商品本身；
# 风格同理：标题常把几种风格词堆在一起（Modern Farmhouse Rustic Industrial），而报告里风格只按主风格归类（vision.main_style）。
# 有主图识别时这些维度只看主图，没有主图识别（没配 DeepSeek）时才看标题
IMAGE_FIRST_DIMENSIONS = ("颜色", "风格")


# 主材质：板材 / 实木 / 铁木（金属 + 木）/ 金属。标题没写清楚的归为“木质（未注明）”或“未注明”。
MATERIAL_CLASSES = ("铁木（金属+木）", "实木", "板材", "木质（未注明）", "金属", "未注明")
_SOLID = re.compile(r"solid (?:wood|oak|pine|acacia|walnut|rubberwood|mango|teak|birch|maple)|real wood|hardwood|"
                    r"acacia|rubberwood|rubber wood|\bpine\b|mango wood|paulownia|bamboo|\bteak\b", re.I)
_PANEL = re.compile(r"engineered wood|\bmdf\b|particle ?board|manufactured wood|chipboard|melamine|laminate", re.I)
_WOOD = re.compile(r"\bwood(?:en)?\b|wood ?grain|walnut|\boak\b|farmhouse", re.I)
_METAL = re.compile(r"\bmetal\b|\bsteel\b|\biron\b|aluminum|wrought", re.I)


def main_material(title: str) -> str:
    title = title or ""
    solid, panel, wood, metal = (bool(rx.search(title)) for rx in (_SOLID, _PANEL, _WOOD, _METAL))
    if metal and (solid or panel or wood):
        return "铁木（金属+木）"
    if solid:
        return "实木"
    if panel:
        return "板材"
    if wood:
        return "木质（未注明）"
    if metal:
        return "金属"
    return "未注明"


def known_brand_key(row: dict) -> str | None:
    """独立品牌证据只使用已知品牌；缺失不通过 ASIN 补成一个新品牌。"""
    return (row.get("brand") or "").strip().lower() or None


def brand_key(row: dict) -> str:
    """需要保留未知品牌商品的分组键；独立品牌支持数必须用 known_brand_key。"""
    return known_brand_key(row) or f"asin:{row.get('asin') or id(row)}"


def extract(title: str) -> dict[str, set[str]]:
    title = title or ""
    return {dim: {label for label, rx in rules if rx.search(title)} for dim, rules in _COMPILED.items()}


def shares(baseline: list[dict], focus: list[dict]) -> dict[str, dict[str, dict]]:
    """标题里每个特征在增长商品和全部头部商品中的占比（不筛选）；brands = 增长商品里有这个特征的品牌数。"""
    nf, nb = len(focus), len(baseline)
    if not nf or not nb:
        return {}
    feats_f = [extract(r.get("title", "")) for r in focus]
    feats_b = [extract(r.get("title", "")) for r in baseline]
    out: dict[str, dict[str, dict]] = {}
    for dim in LEXICON:
        cf = Counter(label for f in feats_f for label in f[dim])
        cb = Counter(label for f in feats_b for label in f[dim])
        brands = {label: len({key for r, f in zip(focus, feats_f)
                             if label in f[dim] and (key := known_brand_key(r)) is not None}) for label in cf}
        out[dim] = {label: {"label": label, "count": n, "share": round(n / nf, 3), "brands": brands[label],
                            "baseline_share": round(cb.get(label, 0) / nb, 3)} for label, n in cf.items()}
    return out


def compare(baseline: list[dict], focus: list[dict], *, per_dim: int = 5, min_count: int | None = None,
            min_share: float = 0.15, min_lift: float = 1.3) -> dict[str, list[dict]]:
    """每个维度里，增长商品中明显偏多的特征（按 提升倍数 × 占比 排序）。
    条件：至少 min_count 个增长商品的标题有、占比 ≥ min_share、占比是全部头部商品的 min_lift 倍以上（加 0.5 平滑）。"""
    nf, nb = len(focus), len(baseline)
    if nf < 3 or nb < 20:
        return {}
    feats_f = [extract(r.get("title", "")) for r in focus]
    feats_b = [extract(r.get("title", "")) for r in baseline]
    if min_count is None:
        min_count = 2 if nf < 15 else 3
    out: dict[str, list[dict]] = {}
    for dim in LEXICON:
        cf = Counter(label for f in feats_f for label in f[dim])
        cb = Counter(label for f in feats_b for label in f[dim])
        rows = []
        for label, count in cf.items():
            share, base_share = count / nf, cb.get(label, 0) / nb
            lift = ((count + 0.5) / (nf + 1)) / ((cb.get(label, 0) + 0.5) / (nb + 1))
            if count >= min_count and share >= min_share - 1e-9 and lift >= min_lift:
                examples = [r.get("asin") for r, f in zip(focus, feats_f) if label in f[dim]][:6]
                rows.append({"label": label, "count": count, "share": round(share, 3),
                             "baseline_share": round(base_share, 3), "lift": round(lift, 2),
                             "asins": examples})
        rows.sort(key=lambda r: -(r["lift"] * r["share"]))
        if rows:
            out[dim] = rows[:per_dim]
    return out
