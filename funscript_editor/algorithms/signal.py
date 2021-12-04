import bisect
import copy
import numpy as np
import logging
import enum

from typing import List
from dataclasses import dataclass
from numpy.linalg import norm

@dataclass
class signalParameter:
    avg_sec_for_local_min_max_extraction: float = 2.0
    additional_points_merge_time_threshold_in_ms: float = 110.0
    additional_points_merge_distance_threshold: float = 10.0
    distance_minimization_threshold: float = 20.0
    high_second_derivative_points_threshold: float = 1.2
    direction_change_filter_len: int = 3


class Signal:

    def __init__(self, fps):
        self.params = signalParameter()
        self.fps = fps
        self.logger = logging.getLogger(__name__)


    class BasePointAlgorithm(enum.Enum):
        """ Available base points algorithm for the decimate process """
        direction_changes = 1
        local_min_max = 2


    class AdditionalPointAlgorithm(enum.Enum):
        """ Available additional point algorithms for the decimate process """
        high_second_derivative = 1
        distance_minimization = 2


    @staticmethod
    def moving_average(x: list, w: int) -> list:
        """ Calculate moving average for given signal x with window size w

        Args:
            x (list): list with float or int signal values
            w (int): window size

        Returns:
            list: moving average for signal x
        """
        w = round(w)

        if len(x) == 0:
            return []

        if len(x) <= w+1:
            return[np.mean(x) for _ in range(len(x))]

        if w == 1:
            return x

        avg = np.convolve(x, np.ones(int(w*2)), 'valid') / int(w*2)
        return [avg[0] for _ in range(int(w))] \
                + list(avg) \
                + [avg[-1] for _ in range(len(avg)+w,len(x))]


    @staticmethod
    def moving_standard_deviation(x: list, w: int) -> list:
        """ Get 2 seconds moving standard deviation of given signal

        Args:
            x (list): input signal
            w (int): window size

        Returns:
            list: moving standard deviation
        """
        w = round(w)

        if len(x) == 0:
            return []

        if len(x)-w <= w:
            return[np.std(x) for _ in range(len(x))]

        std = [np.std(x[ii-w:ii+w+1]) for ii in range(w,len(x)-w)]
        return [std[0] for _ in range(w)] \
                + list(std) \
                + [std[-1] for _ in range(w)]


    @staticmethod
    def first_derivative(x: list, w: int = 1) -> list:
        """ Get first derivative of given signal

        Args:
            x (list): input signal
            w (int): window size for signal smothing

        Returns:
            list: first derivative
        """
        if w < 0:
            w = 1

        if w % 2 == 0:
            w += 1

        return Signal.moving_average(np.diff(x, 1).tolist(), w)


    @staticmethod
    def second_derivative(x: list, w: int = 1) -> list:
        """ Get second derivative of given signal

        Args:
            x (list): input signal
            w (int): window size for signal smothing

        Returns:
            list: second derivative
        """
        if w < 0:
            w = 1

        if w % 2 == 0:
            w += 1

        return Signal.moving_average(np.diff(Signal.moving_average(np.diff(x, 1).tolist(), w), 1).tolist(), w)


    @staticmethod
    def scale(signal: list, lower: float = 0, upper: float = 99) -> list:
        """ Scale an signal (list of float or int) between given lower and upper value

        Args:
            signal (list): list with float or int signal values to scale
            lower (float): lower scale value
            upper (float): upper scale value

        Returns:
            list: list with scaled signal
        """
        if len(signal) == 0:
            return signal

        if len(signal) == 1:
            return [lower]

        signal_min = min(signal)
        signal_max = max(signal)
        return [(float(upper) - float(lower)) * (x - signal_min) / (signal_max - signal_min) + float(lower) for x in signal]


    @staticmethod
    def scale_with_anomalies(
            signal :list,
            lower: float = 0,
            upper: float = 99,
            lower_quantile: float = 0.0005,
            upper_quantile: float = 0.9995) -> list:
        """ Scale an signal (list of float or int) between given lower and upper value

        Args:
            signal (list): list with float or int signal values to scale
            lower (float): lower scale value
            upper (float): upper scale value
            lower_quantile (float): lower quantile value to filter [0,1]
            upper_quantile (float): upper quantile value to filter [0,1]

        Returns:
            list: list with scaled signal
        """
        if len(signal) == 0:
            return signal

        if len(signal) == 1:
            return [lower]

        a1 = np.quantile(signal, lower_quantile)
        a2 = np.quantile(signal, upper_quantile)
        anomaly_free = np.array([x for x in signal if a1 < x < a2])
        anomaly_free_min = min(anomaly_free)
        anomaly_free_max = max(anomaly_free)
        scaled = [(upper - lower) * (x - anomaly_free_min) / (anomaly_free_max - anomaly_free_min) + lower for x in signal]
        return [min((anomaly_free_max, max((anomaly_free_min, x)) )) for x in scaled]


    @staticmethod
    def find_nearest(array: list, value: float, side: str) -> float:
        """ Find nearest value in SORTED array

        Args:
            array (list): sorted list with values
            value (float): search value
            side (str): 'left' or 'right'

        Returns:
            float: nearest value in array
        """
        if side.lower() == 'left':
            pos = 0
            for i in range(len(array)):
                if value <= array[i]:
                    break
                pos = i

            return array[pos]

        elif side.lower() == 'right':
            pos = len(array) - 1
            for i in reversed(range(len(array))):
                if value >= array[i]:
                    break
                pos = i

            return array[pos]
        else:
            raise NotImplementedError("find_nearest is not implemented for side={}".format(side))


    def get_high_second_derivative_points(self, signal: list, alpha: float = 1.2) -> list:
        """ Get change points by comparing second derivative with the rolling standard deviation

        Args:
            signal (list): list with float or int signal values
            alpha (float): threshold value for standard deviation comparing

        Returns:
            list: idx list with changepoints
        """
        dx2 = Signal.second_derivative(signal)
        dx2_abs = abs(np.array(dx2))
        std = Signal.moving_standard_deviation(dx2, round(self.fps * self.params.avg_sec_for_local_min_max_extraction))
        changepoints, tmp_max_idx = [], -1
        for pos in range(len(dx2_abs)):
            if abs(dx2_abs[pos]) > alpha*std[pos]:
                if tmp_max_idx < 0 or dx2_abs[tmp_max_idx] <= dx2_abs[pos]:
                    tmp_max_idx = pos
            elif tmp_max_idx >= 0:
                changepoints.append(tmp_max_idx)
                tmp_max_idx = -1

        return changepoints


    def get_edge_points(self, signal: list, base_points: list, threshold: float = 25.0) -> list:
        """ Get Edge Points by calculate the distance to each point in the signal.

        Note:
            We map the time axis between each predicted base points to min_pos - max_pos in
            this section to get usable distances.

        Args:
            signal (list): the predicted signal
            base_points (list): current base points
            threshold (float): threshold value to predict additional edge point

        Returns:
            list: list with index of the edge points (additional points)
        """
        if len(base_points) < 2:
            return []

        base_points.sort()
        edge_points, overall_max_distance = [], 0
        for i in range(len(base_points) - 1):
            min_pos = min([signal[base_points[i]], signal[base_points[i+1]]])
            max_pos = max([signal[base_points[i]], signal[base_points[i+1]]])

            start = np.array([min_pos, signal[base_points[i]]])
            end = np.array([max_pos, signal[base_points[i+1]]])

            scale = lambda x: ((max_pos - min_pos) * (x - base_points[i]) / float(base_points[i+1] - base_points[i]) + float(min_pos))

            distances = [ \
                    norm( np.cross(end - start, start - np.array([scale(j), signal[j]])) ) / norm(end - start) \
                    for j in range(base_points[i], base_points[i+1])
                ]

            max_distance = max(distances)

            if overall_max_distance < max_distance:
                overall_max_distance = max_distance

            if max_distance > threshold:
                edge_points.append(base_points[i] + distances.index(max_distance))

        self.logger.info("Max distance was {}".format(overall_max_distance))
        return edge_points



    def get_local_min_max_points(self, signal: list, filter_len: int = 3) -> list:
        """ Get the local max and min positions in given signal

        Args:
            signal (list): list with float signal
            filter_len (list): lenght of the moving average window to reduce false positive

        Returns:
            list:  with local max and min indexes
        """
        avg = Signal.moving_average(signal, w=round(self.fps * self.params.avg_sec_for_local_min_max_extraction))
        smothed_signal = Signal.moving_average(signal, w=filter_len)
        points, tmp_min_idx, tmp_max_idx = [], -1, -1
        for pos in range(len(smothed_signal)):
            if smothed_signal[pos] < avg[pos]:
                if tmp_min_idx < 0:
                    tmp_min_idx = pos
                elif smothed_signal[tmp_min_idx] >= smothed_signal[pos]:
                    tmp_min_idx = pos
            elif tmp_min_idx >= 0:
                points.append(tmp_min_idx)
                tmp_min_idx = -1

            if smothed_signal[pos] > avg[pos]:
                if tmp_max_idx < 0:
                    tmp_max_idx = pos
                elif smothed_signal[tmp_max_idx] <= smothed_signal[pos]:
                    tmp_max_idx = pos
            elif tmp_max_idx >= 0:
                points.append(tmp_max_idx)
                tmp_max_idx = -1

        return points


    @staticmethod
    def get_direction_changes(signal: list, filter_len: int = 3) -> list:
        """ Get direction changes positions in given signal

        Args:
            signal (list): list with float signal
            filter_len (int): length of the filter to detect an direction change

        Returns:
            list:  indexes of direction changes
        """
        if len(signal) < filter_len:
            return []

        filtered_indexe = [i for i in range(len(signal)-filter_len) \
                if all(signal[i+j] > signal[i+j+1] for j in range(filter_len)) \
                or all(signal[i+j] < signal[i+j+1] for j in range(filter_len))
        ]

        if len(filtered_indexe) < 2:
            return []

        direction = [-1 if signal[filtered_indexe[i]] > signal[filtered_indexe[i+1]] else 1 \
                for i in range(len(filtered_indexe) - 1)]

        changepoints, start_position, current_direction = [], min(filtered_indexe), direction[0]
        for idx, direction in zip(filtered_indexe, direction):
            if direction != current_direction:
                changepoints.append(idx + start_position)
                current_direction = direction

        return changepoints


    def merge_points(self, signal: list, base_points: list, additional_points: list) -> list:
        """ Merge additional points with base points with given criteria from config file

        Args:
            signal (list): the raw signal
            base_points (list): list wit all base positions
            additional_points (list): additional point candidates

        Returns:
            list: with merged points
        """
        merged_points = copy.deepcopy(base_points)
        merged_points.sort()

        merge_time_threshold = max([ 1, (round(self.fps * self.params.additional_points_merge_time_threshold_in_ms) / 1000.0) ])

        merge_counter = 0
        for idx in additional_points:
            if len(list(filter(lambda x: abs(idx - x) <= merge_time_threshold, merged_points))) > 0:
                continue

            p1 = int(self.find_nearest(merged_points, idx, 'left'))
            p2 = int(self.find_nearest(merged_points, idx, 'right'))

            if p1 >= p2:
                continue

            min_pos = min([signal[p1], signal[p2]])
            max_pos = max([signal[p1], signal[p2]])

            start = np.array([min_pos, signal[p1]])
            end = np.array([max_pos, signal[p2]])

            scale = lambda x: ((max_pos - min_pos) * (x - p1) / float(p2 - p1) + float(min_pos))
            distance = norm( np.cross(end - start, start - np.array([scale(idx), signal[idx]])) ) / norm(end - start)

            if distance < self.params.additional_points_merge_distance_threshold:
                continue

            bisect.insort(merged_points, idx)
            merge_counter += 1

        self.logger.info("Merge %d additional points", merge_counter)
        return merged_points


    def categorize_points(self, signal: list, points: list) -> dict:
        """ Group a list of points in min and max points groups

        Args:
            signal (list): raw signal data
            points (list): list with all point indexes

        Returns:
            dict: dictionary with grouped points { 'min': [], 'max': [] }
        """
        avg = Signal.moving_average(signal, w=round(self.fps * self.params.avg_sec_for_local_min_max_extraction))
        smothed_signal = Signal.moving_average(signal, w=3)

        grouped_points = {'min': [], 'max': []}
        for idx in points:
            if smothed_signal[idx] > avg[idx]:
                grouped_points['max'].append(idx)
            else:
                grouped_points['min'].append(idx)

        return grouped_points


    @staticmethod
    def apply_manual_shift(point_group: dict, max_idx: int, shift: dict = {'min': 0, 'max': 0}) -> dict:
        """ Shift grouped points by given value

        Args:
            point_group (dict): grouped points to apply manual shift
            max_idx (int): max index of the shiftted values (max available idx in the raw signal)
            shift (dict): dictionary with values to shift of each group

        Returns:
            dict: shiftted point group
        """
        for key in shift.keys():
            if key not in point_group:
                continue

            point_group[key] = [ max(( 0, min(( max_idx, point_group[key]+shift[key] )) )) ]

        return point_group


    def decimate(self,
            signal: list,
            base_point_algorithm: BasePointAlgorithm,
            additional_points_algorithms: List[AdditionalPointAlgorithm]) -> list:
        """ Compute the decimated signal with given algorithms

        Args:
            signal (list): raw signal
            base_point_algorithm (BasePointAlgorithm): algorithm to determine the base points
            additional_points_algorithms (List[AdditionalPointAlgorithm]): list with algorithms to determine additional points

        Returns:
            list: indexes for decimated signal
        """
        if base_point_algorithm == self.BasePointAlgorithm.direction_changes:
            decimated_indexes = self.get_direction_changes(signal, filter_len = self.params.direction_change_filter_len)
        elif base_point_algorithm == self.BasePointAlgorithm.local_min_max:
            decimated_indexes = self.get_local_min_max_points(signal)
        else:
            raise NotImplementedError("Selected Base Point Algorithm is not implemented")

        for algo in additional_points_algorithms:
            if algo == self.AdditionalPointAlgorithm.high_second_derivative:
                additional_indexes = self.get_high_second_derivative_points(signal, alpha = self.params.high_second_derivative_points_threshold)
                self.logger.info("Additional Points: high second derivative found %d new point candidates", len(additional_indexes))
            elif algo == self.AdditionalPointAlgorithm.distance_minimization:
                additional_indexes = self.get_edge_points(signal, decimated_indexes, threshold = self.params.distance_minimization_threshold)
                self.logger.info("Additional Points: distance minimization found %d new point candidates", len(additional_indexes))
            else:
                raise NotImplementedError("Selected Additional Points Algorithm is not implemented")

            if len(additional_indexes) > 0:
                decimated_indexes = self.merge_points(signal, decimated_indexes, additional_indexes)

        return decimated_indexes
