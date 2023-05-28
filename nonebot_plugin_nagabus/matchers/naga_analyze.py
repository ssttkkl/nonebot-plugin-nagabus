import re
from urllib.parse import urlparse, parse_qs

from nonebot import on_command, logger, Bot
from nonebot.adapters.onebot.v11 import MessageEvent, Message, MessageSegment
from nonebot.internal.matcher import Matcher
from nonebot.params import CommandArg
from nonebot_plugin_access_control.errors import RateLimitedError
from tensoul.downloader import MajsoulDownloadError

from .errors import BadRequestError, handle_error
from ..ac import ac
from ..naga import naga
from ..naga.service import InvalidKyokuHonbaError, UnsupportedGameError
from ..utils.integer import decode_integer
from ..utils.nonebot import default_cmd_start

analyze_srv = ac.create_subservice("analyze")

uuid_reg = re.compile(r"\d{6}-[\da-fA-F]{8}-[\da-fA-F]{4}-[\da-fA-F]{4}-[\da-fA-F]{4}-[\da-fA-F]{12}")

kyoku_honba_reg = re.compile(r"([东南])([一二三四1234])局([0123456789零一两二三四五六七八九十百千万亿]+)本场")


async def analyze_majsoul(bot: Bot, event: MessageEvent, matcher: Matcher, uuid: str, kyoku: int, honba: int):
    token = await analyze_srv.acquire_token_for_rate_limit(bot, event)
    if token is None:
        raise RateLimitedError()

    try:
        report, cost_np = await naga.analyze_majsoul(uuid, kyoku, honba, event.user_id)
        msg = f"https://naga.dmv.nico/htmls/{report.report_id}.html?tw=0\n"

        if cost_np == 0:
            msg += "由于此前已解析过该局，本次解析消耗0NP"
            await token.retire()
        else:
            msg += f"本次解析消耗{cost_np}NP"

        await matcher.send(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text(msg)
        ]))
    except MajsoulDownloadError as e:
        await token.retire()
        logger.opt(colors=True).warning(f"Failed to download paipu <y>{uuid}</y>, code: {e.code}")
        if e.code == 1203:
            raise BadRequestError("牌谱不存在") from e
        else:
            raise e
    except InvalidKyokuHonbaError as e:
        await token.retire()
        kyoku_honba = []
        for kyoku, honba in e.available_kyoku_honba:
            if kyoku <= 3:
                kyoku_honba.append(f"东{kyoku}局{honba}本场")
            else:
                kyoku_honba.append(f"南{kyoku - 3}局{honba}本场")

        raise BadRequestError(f"请输入正确的场次与本场（{'、'.join(kyoku_honba)}）") from e
    except UnsupportedGameError as e:
        raise BadRequestError("只支持四麻牌谱") from e


async def analyze_tenhou(bot: Bot, event: MessageEvent, matcher: Matcher, haihu_id: str, seat: int):
    token = await analyze_srv.acquire_token_for_rate_limit(bot, event)
    if token is None:
        raise RateLimitedError()

    report, cost_np = await naga.analyze_tenhou(haihu_id, seat, event.user_id)
    msg = f"https://naga.dmv.nico/htmls/{report.report_id}.html?tw=0\n"

    if cost_np == 0:
        msg += "由于此前已解析过该局，本次解析消耗0NP"
        await token.retire()
    else:
        msg += f"本次解析消耗{cost_np}NP"

    await matcher.send(Message([
        MessageSegment.reply(event.message_id),
        MessageSegment.text(msg)
    ]))


naga_analyze_matcher = on_command("naga", priority=10)


@naga_analyze_matcher.handle()
@handle_error(naga_analyze_matcher)
async def naga_analyze(bot: Bot, event: MessageEvent, matcher: Matcher, cmd_args=CommandArg()):
    if not await analyze_srv.check(bot, event, acquire_rate_limit_token=False):
        await matcher.finish()

    args = cmd_args.extract_plain_text().split(' ')
    if "maj-soul" in args[0]:
        mat = uuid_reg.search(args[0])
        if not mat:
            raise BadRequestError("不正确的雀魂牌谱")

        uuid = mat.group(0)

        if len(args) < 2:
            raise BadRequestError("请指定场次与本场")

        kyoku = None
        honba = None

        mat = kyoku_honba_reg.search(args[1])
        if not mat:
            raise BadRequestError("请输入正确的场次与本场")

        raw_wind, raw_kyoku, raw_honba = mat.groups()

        try:
            kyoku = decode_integer(raw_kyoku) - 1
            if raw_wind == '南':
                kyoku += 4

            honba = decode_integer(raw_honba)
        except ValueError:
            pass

        if kyoku is None or honba is None:
            raise BadRequestError("请输入正确的场次与本场")

        await analyze_majsoul(bot, event, matcher, uuid, kyoku, honba)
    elif "tenhou" in args[0]:
        tenhou_url = args[0].strip()

        _, _, _, _, tenhou_query, _ = urlparse(tenhou_url)
        tenhou_query = parse_qs(tenhou_query)

        haihu_id = tenhou_query["log"][0]
        seat = 0
        if "tw" in tenhou_query and len(tenhou_query["tw"]) > 0:
            seat = int(tenhou_query["tw"][0])

        await analyze_tenhou(bot, event, matcher, haihu_id, seat)
    else:
        await matcher.send(Message([
            MessageSegment.reply(event.message_id),
            MessageSegment.text("用法：\n"
                                f"{default_cmd_start}naga <雀魂牌谱链接> <东/南x局x本场>：消耗10NP解析雀魂小局\n"
                                f"{default_cmd_start}naga <天凤牌谱链接>：消耗50NP解析天凤半庄")
        ]))