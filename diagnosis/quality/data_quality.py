# -*- coding: utf-8 -*-

"""
Industrial vibration data quality inspection module.

Function:
    Before model inference:
        signal
            |
            ↓
        DataQualityChecker
            |
            ↓
        valid / invalid

Designed for:
    - bearing fault diagnosis
    - rotating machinery monitoring
    - online health monitoring platform

"""

from dataclasses import dataclass
from typing import Dict, List

import numpy as np



# =====================================================
# 质量检测结果
# =====================================================

@dataclass
class QualityResult:

    valid: bool

    status: str

    reasons: List[str]

    metrics: Dict



    def to_dict(self):

        return {

            "valid":
            self.valid,


            "status":
            self.status,


            "reasons":
            self.reasons,


            "metrics":
            self.metrics

        }




# =====================================================
# 数据质量检测器
# =====================================================

class DataQualityChecker:


    def __init__(

        self,

        min_length=4096,

        min_std=1e-8,

        max_zero_fraction=0.98,

        max_flat_fraction=0.98,

        max_clip_fraction=0.05,

        clip_threshold=None

    ):


        """
        Parameters
        ----------
        min_length:
            最少采样点数量

        min_std:
            最小标准差
            用于检测传感器脱落


        max_zero_fraction:
            最大零值比例


        max_flat_fraction:
            最大平坦比例


        max_clip_fraction:
            最大饱和比例


        clip_threshold:
            ADC饱和值

        """

        self.min_length=min_length

        self.min_std=min_std

        self.max_zero_fraction=max_zero_fraction

        self.max_flat_fraction=max_flat_fraction

        self.max_clip_fraction=max_clip_fraction

        self.clip_threshold=clip_threshold



    # =================================================
    # 主接口
    # =================================================

    def check(

        self,

        signal,

        sampling_rate=None,

        rpm=None

    )->QualityResult:


        reasons=[]


        metrics={}



        # ---------------------------------------------
        # 1. 基础格式检查
        # ---------------------------------------------


        try:

            x=np.asarray(

                signal,

                dtype=np.float32

            ).reshape(-1)


        except Exception:


            return QualityResult(

                False,

                "DATA_ERROR",

                ["cannot_convert_signal"],

                {}

            )



        metrics["length"]=int(len(x))



        # 空数据

        if len(x)==0:


            return QualityResult(

                False,

                "DATA_EMPTY",

                ["empty_signal"],

                metrics

            )



        # ---------------------------------------------
        # 2. NaN / Inf检测
        # ---------------------------------------------


        if not np.isfinite(x).all():
            return QualityResult(False, "REJECT", ["nan_or_inf"], metrics)

        # Accumulate statistics in float64 to avoid overflow of finite float32 samples.
        x = x.astype(np.float64)

        # ---------------------------------------------
        # 3. 长度检测
        # ---------------------------------------------


        if len(x)<self.min_length:


            reasons.append(

                "signal_too_short"

            )



        # ---------------------------------------------
        # 4. 信号统计指标
        # ---------------------------------------------


        mean=float(
            np.mean(x)
        )

        std=float(
            np.std(x)
        )

        rms=float(
            np.sqrt(
                np.mean(
                    x*x
                )
            )
        )


        peak=float(
            np.max(
                np.abs(x)
            )
        )



        metrics.update(

            {

            "mean":
            mean,


            "std":
            std,


            "rms":
            rms,


            "peak":
            peak

            }

        )



        # ---------------------------------------------
        # 5. 传感器脱落检测
        # ---------------------------------------------


        if std < self.min_std:


            reasons.append(

                "signal_flat_sensor_loss"

            )



        # ---------------------------------------------
        # 6. 零值比例
        # ---------------------------------------------


        zero_fraction=float(

            np.mean(

                np.abs(x)<1e-12

            )

        )


        metrics["zero_fraction"]=zero_fraction



        if zero_fraction > self.max_zero_fraction:


            reasons.append(

                "too_many_zero_values"

            )



        # ---------------------------------------------
        # 7. 数据冻结检测
        # ---------------------------------------------
        #
        # 例如：
        # 0.123456
        # 0.123456
        # 0.123456
        #
        # 可能ADC卡死
        #


        if len(x)>10:


            diff=np.abs(

                np.diff(x)

            )


            flat_fraction=float(

                np.mean(

                    diff < 1e-12

                )

            )


        else:


            flat_fraction=1.0



        metrics["flat_fraction"]=flat_fraction



        if flat_fraction > self.max_flat_fraction:


            reasons.append(

                "signal_frozen"

            )



        # ---------------------------------------------
        # 8. 饱和检测
        # ---------------------------------------------


        if self.clip_threshold is not None:


            clip_fraction=float(

                np.mean(

                    np.abs(x)>=self.clip_threshold

                )

            )


            metrics["clip_fraction"]=clip_fraction



            if clip_fraction > self.max_clip_fraction:


                reasons.append(

                    "adc_saturation"

                )



        else:


            metrics["clip_fraction"]=0.0



        # ---------------------------------------------
        # 9. 转速检查
        # ---------------------------------------------


        if rpm is not None:


            if (

                not np.isfinite(rpm)

                or rpm<=0

            ):


                reasons.append(

                    "invalid_rpm"

                )


            metrics["rpm"]=float(rpm)



        # ---------------------------------------------
        # 10. 采样率检查
        # ---------------------------------------------


        if sampling_rate is not None:


            if sampling_rate<=0:


                reasons.append(

                    "invalid_sampling_rate"

                )


            metrics["sampling_rate"]=float(
                sampling_rate
            )



        # =================================================
        # 最终判断
        # =================================================


        if len(reasons)==0:


            return QualityResult(

                True,

                "OK",

                [],

                metrics

            )


        else:


            return QualityResult(

                False,

                "REJECT",

                reasons,

                metrics

            )




# =====================================================
# 方便外部调用的函数
# =====================================================


_default_checker=DataQualityChecker()



def check_signal_quality(

    signal,

    sampling_rate=None,

    rpm=None,

    **kwargs

):


    """
    外部统一接口


    Example:

    result=check_signal_quality(
        signal,
        sampling_rate=16000,
        rpm=2400
    )

    """


    checker=DataQualityChecker(

        **kwargs

    )


    return checker.check(

        signal,

        sampling_rate,

        rpm

    ).to_dict()




# =====================================================
# 单元测试
# =====================================================


if __name__=="__main__":


    print(
        "===== Normal signal ====="
    )


    sig=np.random.randn(
        16000
    )


    result=check_signal_quality(

        sig,

        sampling_rate=16000,

        rpm=2400

    )


    print(result)



    print(
        "\n===== Zero signal ====="
    )


    sig=np.zeros(
        16000
    )


    result=check_signal_quality(

        sig,

        sampling_rate=16000,

        rpm=2400

    )


    print(result)



    print(
        "\n===== NaN signal ====="
    )


    sig=np.ones(
        16000
    )

    sig[100]=np.nan


    result=check_signal_quality(

        sig,

        sampling_rate=16000,

        rpm=2400

    )


    print(result)
