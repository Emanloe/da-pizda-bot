"""
Expedition expiration checker - runs periodically to check and complete expired expeditions.
"""

import logging
from datetime import datetime, timezone

from telegram.ext import ContextTypes

from handlers.adventure import (
    load_player_data,
    save_player_data,
    send_expedition_result,
)

logger = logging.getLogger(__name__)


async def expedition_expiration_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Periodic job that checks for expired expeditions and sends results.
    Called every minute.
    """
    now = datetime.now(timezone.utc)
    
    try:
        player_data = load_player_data()
        
        for user_id, player in player_data.items():
            active_expedition = player.get("active_expedition")
            
            if not active_expedition:
                continue
            
            # Check if expedition has expired
            end_time_str = active_expedition.get("end_time")
            if not end_time_str:
                continue
            
            try:
                end_time = datetime.fromisoformat(end_time_str)
            except (ValueError, TypeError) as e:
                logger.error(f"Error parsing end_time for user {user_id}: {e}")
                continue
            
            if now >= end_time:
                # Expedition has expired, send result
                try:
                    await send_expedition_result(context.bot, int(user_id))
                    # Clear active expedition
                    player["active_expedition"] = None
                    logger.info(f"Expedition completed for user {user_id}")
                except Exception as e:
                    logger.error(f"Error sending expedition result for user {user_id}: {e}")
        
        save_player_data(player_data)
        
    except Exception as e:
        logger.error(f"Error in expedition_expiration_job: {e}")
