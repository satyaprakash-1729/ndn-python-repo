"""
    Concurrent segment fetcher.

    @Author jonnykong@cs.ucla.edu
    @Date   2019-10-15
"""

import asyncio as aio
from typing import Optional
from ndn.app import NDNApp
from ndn.types import InterestNack, InterestTimeout
from ndn.encoding import Name


async def concurrent_fetcher(app: NDNApp, name, start_block_id: Optional[int],
                             end_block_id: Optional[int], semaphore: aio.Semaphore):
    """
    An async-generator to fetch segmented object. Interests are issued concurrently.
    :param app: NDNApp
    :param name: Name prefix of Data
    :return: Data segments in order
    """
    cur_id = start_block_id if start_block_id is not None else 0
    final_id = end_block_id if end_block_id is not None else 0x7fffffff
    is_failed = False
    tasks = []
    recv_window = cur_id - 1
    seq_to_bytes = dict()           # Buffer for out-of-order delivery
    received_or_fail = aio.Event()  #

    async def retry(seq: int):
        """
        Retry
        :param seq:
        :param after_fetched:
        :return:
        """
        nonlocal app, name, semaphore, is_failed, received_or_fail
        int_name = name[:]
        int_name.append(str(seq))

        trial_times = 0
        while True:
            trial_times += 1
            if trial_times > 3:
                semaphore.release()
                is_failed = True
                received_or_fail.set()
                return
            try:
                print('Express Interest: {}'.format(Name.to_str(Name.normalize(int_name))))
                data_name, meta_info, content = await app.express_interest(
                    int_name, must_be_fresh=True, can_be_prefix=False, lifetime=1000)
                print('Received data: {}'.format(Name.to_str(data_name)))
                seq_to_bytes[seq] = content
                break
            except InterestNack as e:
                print(f'Nacked with reason={e.reason}')
            except InterestTimeout:
                print(f'Timeout')
        semaphore.release()
        received_or_fail.set()

    async def dispatch_tasks():
        nonlocal semaphore, tasks, cur_id, final_id, is_failed
        while cur_id <= final_id:
            await semaphore.acquire()
            if is_failed:
                received_or_fail.set()
                semaphore.release()
                break
            task = aio.get_event_loop().create_task(retry(cur_id))
            tasks.append(task)
            cur_id += 1

    aio.create_task(dispatch_tasks())
    while True:
        await received_or_fail.wait()
        received_or_fail.clear()
        # Re-assemble bytes in order
        while recv_window + 1 in seq_to_bytes:
            yield seq_to_bytes[recv_window + 1]
            recv_window += 1
        if recv_window == final_id or is_failed:
            await aio.gather(*tasks)
            return


async def main(app: NDNApp):
    """
    Async helper function to run the concurrent fetcher.
    This function is necessary because it's responsible for calling app.shutdown().
    :param app: NDNApp
    """
    semaphore = aio.Semaphore(1)
    async for content in concurrent_fetcher(app, Name.from_str('/example/testApp'), 0, 50, semaphore):
        print(content)
    app.shutdown()


if __name__ == '__main__':
    app = NDNApp()
    app.run_forever(after_start=main(app))