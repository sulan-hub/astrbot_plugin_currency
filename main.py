from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import json
import os
from datetime import datetime
import aiohttp
import asyncio

@register("currency", "苏澜", "数字货币价格查询和提醒\t【清空提醒后再更改,不然会生成多个循环,容易被封ip】", "1.0.1")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data_dir = "data/plugin_data/currency/"
        self.data_file = self.data_dir + "reminders.json"
        self.reminder_tasks = {}
        self.umo_storage = {}  # 存储 unified_msg_origin

        self.config = self.load_config()
        
        
        os.makedirs(self.data_dir, exist_ok=True)
        
        # 启动时加载保存的提醒任务
        asyncio.create_task(self.load_reminders())


    def load_config(self):
        """从配置文件加载配置"""
        config_file = "data/config/astrbot_plugin_currency_config.json"
        default_config = {"check_interval": 1800}
        
        try:
            if os.path.exists(config_file):
                # 使用 utf-8-sig 编码自动处理 BOM
                with open(config_file, 'r', encoding='utf-8-sig') as f:
                    data = json.load(f)
                    config = {
                        "check_interval": data.get("check_interval", 1800)
                    }
                    logger.info(f"成功加载配置: check_interval={config['check_interval']}秒")
                    return config
            else:
                logger.warning(f"配置文件不存在: {config_file}, 使用默认配置")
                return default_config
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}, 使用默认配置")
            return default_config

    async def fapi(self, event: AstrMessageEvent, symbol: str):
        """获取币种价格"""
        url = f"https://fapi.coinglass.com/api/coin/v2/info?symbol={symbol}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data['code'] == '0':
                            ticker = data['data']
                            price = float(ticker['price'])
                            change_24h = float(ticker['priceChangePercent24h'])
                            change_7d = float(ticker['priceChangePercent7d'])
                            
                            result = f"💰 **{symbol}/USDT 实时价格**\n"
                            result += f"📊 最新价格: ${price:,.2f}\n"
                            result += f"📈 24h涨跌: {change_24h:+.2f}%\n"
                            result += f"🔥 7d涨跌: {change_7d:+.2f}%\n"
                            result += f"🕐 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            
                            yield event.plain_result(result)
                        else:
                            yield event.plain_result(f"❌ API 错误: {data.get('msg', '未知错误')}")
                    else:
                        yield event.plain_result(f"❌ 获取价格失败: HTTP {response.status}")
        except Exception as e:
            logger.error(f"获取价格失败: {e}")
            yield event.plain_result(f"❌ 请求失败: {str(e)}")

    async def get_current_price(self, symbol: str):
        """获取当前价格（不发送消息）"""
        url = f"https://fapi.coinglass.com/api/coin/v2/info?symbol={symbol}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data['code'] == '0':
                            return float(data['data']['price'])
        except Exception as e:
            logger.error(f"获取{symbol}价格失败: {e}")
        return None

    async def tapi(self, unified_msg_origin: str, symbol: str, target_price: float, direction: str, user_id: str):
        """设置价格提醒"""
        task_id = f"{user_id}_{symbol}_{target_price}_{direction}"
        
        while task_id in self.reminder_tasks:
            try:
                # interval = self.config.get("check_interval", 1800)
                current_price = await self.get_current_price(symbol)
                
                if current_price is None:
                    await asyncio.sleep(30)
                    continue
                
                # 判断是否触发提醒
                triggered = False
                if direction == "上" and current_price >= target_price:
                    triggered = True
                    message = f"🔔 **价格提醒**\n{symbol} 已上涨至 ${current_price:,.2f}\n目标价格: ${target_price:,.2f}"
                    logger.info(f"{symbol} 已上涨至 ${current_price:,.2f}")
                elif direction == "下" and current_price <= target_price:
                    triggered = True
                    message = f"🔔 **价格提醒**\n{symbol} 已下跌至 ${current_price:,.2f}\n目标价格: ${target_price:,.2f}"
                    logger.info(f"{symbol} 已下跌至 ${current_price:,.2f}")
                
                if triggered:
                    # 使用存储的 unified_msg_origin 发送主动消息
                    message_chain = MessageChain().message(message)
                    await self.context.send_message(unified_msg_origin, message_chain)
                    
                    # 从任务列表和文件中移除
                    if task_id in self.reminder_tasks:
                        del self.reminder_tasks[task_id]
                        if task_id in self.umo_storage:
                            del self.umo_storage[task_id]
                        self.save_reminders()
                    break
                    
            except asyncio.CancelledError:
                logger.info(f"提醒任务已取消: {task_id}")
                break
            except Exception as e:
                logger.error(f"提醒任务出错: {e}")
                await asyncio.sleep(10)
                continue

            # 修复：正确的日志信息
            logger.info(f"{symbol} 价格提醒检查中，间隔1800秒")
            check_interval = self.config.get("check_interval", 1800)
            logger.info(f"{check_interval}秒")
            await asyncio.sleep(check_interval)  # 检查间隔秒

    def save_reminders(self):
        """保存提醒任务到文件"""
        try:
            reminders = []
            for task_id, task in self.reminder_tasks.items():
                parts = task_id.split('_')
                if len(parts) >= 4:
                    reminders.append({
                        'user_id': parts[0],
                        'symbol': parts[1],
                        'price': float(parts[2]),
                        'direction': parts[3],
                        'umo': self.umo_storage.get(task_id, '')
                    })
            
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(reminders, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存 {len(reminders)} 个提醒任务")
        except Exception as e:
            logger.error(f"保存提醒任务失败: {e}")

    async def load_reminders(self):
        """从文件加载提醒任务"""
        try:
            if not os.path.exists(self.data_file):
                return
            
            with open(self.data_file, 'r', encoding='utf-8') as f:
                reminders = json.load(f)
            
            # 恢复提醒任务
            for reminder in reminders:
                user_id = reminder['user_id']
                symbol = reminder['symbol']
                price = reminder['price']
                direction = reminder['direction']
                umo = reminder['umo']
                
                task_id = f"{user_id}_{symbol}_{price}_{direction}"
                
                # 恢复 umo_storage
                self.umo_storage[task_id] = umo
                
                # 重新创建任务
                task = asyncio.create_task(self.tapi(umo, symbol, price, direction, user_id))
                self.reminder_tasks[task_id] = task
                
            logger.info(f"已恢复 {len(reminders)} 个提醒任务")
            
        except Exception as e:
            logger.error(f"加载提醒任务失败: {e}")

    @filter.command("ETH")
    async def get_eth_price(self, event: AstrMessageEvent):
        '''获取ETH当前价格，格式：/ETH'''
        async for result in self.fapi(event, "ETH"):
            yield result

    @filter.command("BTC")
    async def get_btc_price(self, event: AstrMessageEvent):
        '''获取BTC当前价格，格式：/BTC'''
        async for result in self.fapi(event, "BTC"):
            yield result

    @filter.command("提醒")
    async def set_reminder(self, event: AstrMessageEvent):
        """
        设置价格提醒，格式：/提醒 <价格> <币种> <上/下>
        示例：/提醒 50000 BTC 上 （价格上涨到50000时提醒）
              /提醒 40000 ETH 下 （价格下跌到40000时提醒）
        """
        args = event.message_str.strip().split()
        check_interval = self.config.get("check_interval", 1800)
        
        if len(args) < 4:
            yield event.plain_result(
                "❌ 格式错误！\n"
                "正确格式：/提醒 <价格> <币种> <上/下>\n"
                "示例：/提醒 50000 BTC 上\n"
                "     /提醒 40000 ETH 下"
            )
            return

        try:
            target_price = float(args[1])
            symbol = args[2].upper()
            direction = args[3]
            
            if direction not in ["上", "下"]:
                yield event.plain_result("❌ 方向只能是'上'或'下'！")
                return
            
            # 获取当前价格用于确认
            current_price = await self.get_current_price(symbol)
            if current_price is None:
                yield event.plain_result(f"❌ 无法获取{symbol}当前价格，请检查币种名称")
                return
            
            # 检查提醒是否合理
            if direction == "上" and current_price >= target_price:
                yield event.plain_result(f"⚠️ {symbol} 当前价格 ${current_price:,.2f} 已经高于目标价格 ${target_price:,.2f}")
                return
            elif direction == "下" and current_price <= target_price:
                yield event.plain_result(f"⚠️ {symbol} 当前价格 ${current_price:,.2f} 已经低于目标价格 ${target_price:,.2f}")
                return
            
            # 获取 unified_msg_origin 和 user_id
            umo = event.unified_msg_origin
            user_id = event.get_sender_id()
            task_id = f"{user_id}_{symbol}_{target_price}_{direction}"
            
            # 存储 umo
            self.umo_storage[task_id] = umo
            
            # 启动异步任务
            task = asyncio.create_task(self.tapi(umo, symbol, target_price, direction, user_id))
            self.reminder_tasks[task_id] = task
            
            # 保存到文件
            self.save_reminders()
            
            # 发送确认消息
            yield event.plain_result(
                f"✅ 已设置提醒！\n"
                f"币种: {symbol}\n"
                f"当前价格: ${current_price:,.2f}\n"
                f"目标价格: ${target_price:,.2f}\n"
                f"提醒条件: 价格向{direction}突破\n"
                f"检查间隔: {check_interval}秒"
            )
            
        except ValueError:
            yield event.plain_result("❌ 价格必须是数字！")
        except Exception as e:
            logger.error(f"设置提醒失败: {e}")
            yield event.plain_result(f"❌ 设置提醒失败: {str(e)}")

    @filter.command("取消")
    async def cancel_reminder(self, event: AstrMessageEvent):
        """
        取消价格提醒，格式：/取消 <价格> <币种> <上/下>
        """
        args = event.message_str.strip().split()
        
        if len(args) < 4:
            yield event.plain_result(
                "❌ 格式错误！\n"
                "正确格式：/取消 <价格> <币种> <上/下>\n"
                "查看提醒：/提醒列表\n"
                "示例：/取消 50000 BTC 上"
            )
            return
        
        try:
            target_price = float(args[1])
            symbol = args[2].upper()
            direction = args[3]
            
            if direction not in ["上", "下"]:
                yield event.plain_result("❌ 方向只能是'上'或'下'！")
                return
                
            task_id = f"{event.get_sender_id()}_{symbol}_{target_price}_{direction}"
            
            if task_id in self.reminder_tasks:
                self.reminder_tasks[task_id].cancel()
                del self.reminder_tasks[task_id]
                if task_id in self.umo_storage:
                    del self.umo_storage[task_id]
                self.save_reminders()
                yield event.plain_result(f"✅ 已取消 {symbol} 向{direction}突破 ${target_price:,.2f} 的提醒")
            else:
                # 提供可能的近似匹配
                await self._suggest_similar_reminders(event, symbol, target_price, direction)
                
        except ValueError:
            yield event.plain_result("❌ 价格必须是数字！")
        except Exception as e:
            logger.error(f"取消提醒失败: {e}")
            yield event.plain_result(f"❌ 取消提醒失败: {str(e)}")

    @filter.command("提醒列表")
    async def reminder_list(self, event: AstrMessageEvent):
        """
        查看所有提醒，格式：/提醒列表
        """
        user_id = event.get_sender_id()
        user_tasks = []
        
        for task_id in self.reminder_tasks.keys():
            if task_id.startswith(user_id):
                parts = task_id.split('_')
                if len(parts) >= 4:
                    user_tasks.append({
                        'symbol': parts[1],
                        'price': parts[2],
                        'direction': parts[3]
                    })
        
        if user_tasks:
            result = "📋 **您的提醒列表**\n"
            for i, task in enumerate(user_tasks, 1):
                result += f"{i}. {task['symbol']} 向{task['direction']}突破 ${float(task['price']):,.2f}\n"
            result += "\n取消提醒：/取消 <价格> <币种> <上/下>\n"
            yield event.plain_result(result)
        else:
            yield event.plain_result("📋 您当前没有设置任何提醒\n\n添加提醒：/提醒 <价格> <币种> <上/下>\n示例：/提醒 50000 BTC 上")

    async def _suggest_similar_reminders(self, event, symbol, target_price, direction):
        """当找不到提醒时，提供相似的提醒建议"""
        user_id = event.get_sender_id()
        similar_tasks = []
        
        for task_id in self.reminder_tasks.keys():
            if task_id.startswith(user_id):
                parts = task_id.split('_')
                if len(parts) >= 4 and parts[1] == symbol and parts[3] == direction:
                    similar_tasks.append(float(parts[2]))
        
        if similar_tasks:
            similar_prices = sorted(similar_tasks)
            result = f"❌ 未找到 {symbol} 向{direction}突破 ${target_price:,.2f} 的提醒\n\n"
            result += f"您当前 {symbol} 向{direction}突破的提醒有：\n"
            for price in similar_prices:
                result += f"  • ${price:,.2f}\n"
            yield event.plain_result(result)
        else:
            yield event.plain_result(f"❌ 未找到对应的提醒任务\n\n查看所有提醒：/提醒列表")
