"""优先级、候选灵活度、短加工时间排序后，共用约束解码器。"""
from decoder import decode_schedule

def schedule_station(pending_df, route_equipment_map, station_equipment_map,
        machine_uph, machine_status, machine_unavailable, wip_df, station, current_time):
    jobs=pending_df[pending_df['下一工站']==station].reset_index(drop=True)
    def rank(i):
        job=jobs.iloc[i]
        allowed=job.get('_allowed',route_equipment_map.get((job['工艺路线'],station),[]))
        valid=[m for m in allowed if machine_uph.get(m,0)>0 and m in station_equipment_map.get(station,[])]
        return (-job['优先级'],len(valid),min((job['数量']/machine_uph[m] for m in valid),default=float('inf')))
    return decode_schedule(pending_df,route_equipment_map,station_equipment_map,
        machine_uph,machine_status,machine_unavailable,wip_df,station,current_time,sorted(range(len(jobs)),key=rank))
