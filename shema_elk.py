#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
  SHEMA_ELK.PY — ГЕНЕРАТОР ИНТЕРАКТИВНЫХ ОРТОГОНАЛЬНЫХ СХЕМ (HTML / SVG / ELK)
================================================================================
Назначение:
  Преобразует текстовый файл формального DSL (DIAGRAM / BLOCK / WEST / EAST /
  CONNECT) в автономную интерактивную HTML-страницу с векторной SVG-схемой
  на базе движка elkjs (ELK Engine). Работает ТОЛЬКО на стандартной библиотеке
  Python 3 (дополнительные pip-пакеты и venv НЕ требуются).

СИНТАКСИС ЗАПУСКА:
  python3 shema_elk.py <файл_инструкций> [ОПЦИИ]

ОБЯЗАТЕЛЬНЫЙ ПОЗИЦИОННЫЙ АРГУМЕНТ:
  <файл_инструкций>       Путь к текстовому DSL-файлу (например:
                          shema_instructions.txt). Кодировка UTF-8 / UTF-8 BOM.

ОПЦИИ И ПАРАМЕТРЫ:
  -o, --output <путь>     Куда сохранить итоговый HTML-файл.
                          • Можно указать точный путь к файлу:
                            -o ./build/shema_viewer.html
                          • Можно указать путь к папке (создастся автоматически):
                            -o ./project_dir/ -> ./project_dir/shema_viewer.html
                          • Если не указаны ни -o, ни --beside, файл сохраняется
                            в ТЕКУЩУЮ рабочую директорию: ./shema_viewer.html.
                          • Несовместимо с --beside.

  --beside                Сохранить shema_viewer.html строго в той же папке,
                          где лежит входной файл <файл_инструкций>, независимо
                          от текущей рабочей директории (CWD).
                          Несовместимо с -o / --output.

  --no-open               Не открывать итоговый HTML в браузере после генерации.
                          (В headless Linux-средах без DISPLAY и WAYLAND_DISPLAY
                          или при BROWSER=true / CI=true браузер отключается
                          автоматически даже без этого флага.)

  --strict                Строгий режим для CI и конвейера Grok Build:
                          если при разборе DSL возникло хотя бы одно
                          предупреждение (нормализация ID, неизвестный порт,
                          пропущенная связь и т.д.) ИЛИ найдено 0 блоков /
                          0 связей — HTML НЕ записывается, скрипт завершается
                          с кодом возврата 2.

  --check-only            Только проверить синтаксис и связность DSL-файла и
                          вывести статистику (блоков / связей / предупреждений),
                          не создавая HTML-файл.

  -h, --help              Вывести эту полную справку и завершиться с кодом 0.

ТИПОВЫЕ СЦЕНАРИИ ИСПОЛЬЗОВАНИЯ:
  1) Рекомендуемый запуск в среде Grok Build (в папке проекта, строгий режим):
     python3 /tmp/shema_elk.py shema_instructions.txt --beside --no-open --strict

  2) Запуск в Grok Build с явным указанием пути сохранения в проект:
     python3 /tmp/shema_elk.py shema_instructions.txt -o ./shema_viewer.html --no-open --strict

  3) Обратно совместимый базовый запуск из корня проекта:
     BROWSER=true python3 /tmp/shema_elk.py shema_instructions.txt

  4) Предварительная валидация DSL без записи HTML:
     python3 /tmp/shema_elk.py shema_instructions.txt --check-only --strict

  5) Локальный запуск на ПК (Debian 13 KDE Wayland, от обычного пользователя):
     python3 shema_elk.py shema_instructions.txt
     (Создаст ./shema_viewer.html и автоматически откроет его в браузере.)

КОДЫ ВОЗВРАТА (EXIT CODES):
  0  Успешная генерация (или успешная проверка в режиме --check-only / --help).
  1  Ошибка аргументов, пустой вызов или входной файл не найден (выводится справка).
  2  Ошибка содержимого DSL: 0 блоков, либо провал строгого режима (--strict).

ПРИМЕЧАНИЕ О ПРОСМОТРЕ HTML:
  Для раскладки графа внутри браузера shema_viewer.html подгружает elkjs@0.9.3
  с jsDelivr CDN (нужен интернет при открытии). Кнопка «Скачать SVG» на странице
  сохраняет полностью автономный векторный файл diagram.svg.
================================================================================
"""

import argparse
from collections import defaultdict, deque
import json
import os
import re
import sys
import webbrowser
from pathlib import Path

USAGE_GUIDE = (__doc__ or "").strip()

HEADER_H = 44
ROW_H = 24
BOTTOM_PAD = 10

VALID_THEMES = {
    "dark", "slate", "gray", "red", "orange", "amber",
    "emerald", "teal", "sky", "blue", "indigo", "violet", "rose"
}
HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
HEX_TOKEN_RE = re.compile(r"^#[0-9a-fA-F]{3,8}(?=\s|$)")
KEYWORD_RE = re.compile(
    r"^(DIAGRAM|BLOCK|TITLE|SUBTITLE|THEME|COLOR|CONNECT)(?:\s*:\s*|\s+|$)(.*)$",
    re.IGNORECASE,
)


def strip_inline_comment(line: str) -> str:
    """Удаляет комментарии '# ...' в конце строки, сохраняя #HEX цвета и '#' внутри кавычек."""
    in_quote = None
    for i, ch in enumerate(line):
        if ch in ('"', "'"):
            if in_quote == ch:
                in_quote = None
            elif in_quote is None:
                in_quote = ch
        elif ch == "#" and in_quote is None:
            if i == 0:
                return ""
            if line[i - 1].isspace() and HEX_TOKEN_RE.match(line[i:]):
                continue
            return line[:i].strip()
    return line.strip()


def sanitize_id(raw: str) -> str:
    """Нормализация идентификатора с сохранением различий между '+' и '-'."""
    s = raw.strip().replace("+", "_POS_").replace("~", "_AC_")
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", s)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "id"


def strip_outer_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1].strip()
    return s


def is_valid_color(val: str) -> bool:
    return val in VALID_THEMES or bool(HEX_RE.match(val))


def is_leaf(block: dict) -> bool:
    """Листовой узел графа (имеет ровно 1 порт)."""
    return (len(block["west"]) + len(block["east"])) == 1


def is_two_port(block: dict) -> bool:
    """Двухпортовый узел графа (всего 2 порта)."""
    return (len(block["west"]) + len(block["east"])) == 2


def is_passthrough(block: dict) -> bool:
    """Транзитный узел графа (1 порт на WEST и 1 порт на EAST)."""
    return len(block["west"]) == 1 and len(block["east"]) == 1


def rebuild_pin_side(block: dict):
    """Синхронизирует словарь pin_side строго в порядке: сначала WEST, затем EAST."""
    new_map = {}
    for pid, _ in block["west"]:
        new_map[pid] = "WEST"
    for pid, _ in block["east"]:
        new_map[pid] = "EAST"
    block["pin_side"] = new_map


def find_cascades(blocks: dict, edges: list, hub_ids: set) -> dict:
    """
    Двухпроходное определение функциональных каскадов:
    1. Связность через не-листовые блоки и индивидуальные порты хабов (без прохода сквозь хаб или листья).
    2. Присоединение параллельных блоков, висящих только между листьями, к каскаду их локального листа.
    """
    adj_c = defaultdict(set)
    leaf_neighbors = defaultdict(set)

    for e in edges:
        u, v = e["sources"][0], e["targets"][0]
        ub, vb = u.split(".", 1)[0], v.split(".", 1)[0]
        u_leaf = (ub not in hub_ids) and is_leaf(blocks[ub])
        v_leaf = (vb not in hub_ids) and is_leaf(blocks[vb])

        if u_leaf and not v_leaf and vb not in hub_ids:
            leaf_neighbors[ub].add(vb)
        if v_leaf and not u_leaf and ub not in hub_ids:
            leaf_neighbors[vb].add(ub)

        if u_leaf or v_leaf:
            continue

        nu = f"HUBPORT:{u}" if ub in hub_ids else f"BLOCK:{ub}"
        nv = f"HUBPORT:{v}" if vb in hub_ids else f"BLOCK:{vb}"
        if nu != nv:
            adj_c[nu].add(nv)
            adj_c[nv].add(nu)

    cascade_of = {}
    comp_members = defaultdict(list)
    cid = 0
    for bid, b in blocks.items():
        if bid in hub_ids or is_leaf(b):
            continue
        if bid in cascade_of:
            continue
        cid += 1
        start = f"BLOCK:{bid}"
        q = deque([start])
        visited_c = {start}
        while q:
            curr = q.popleft()
            if curr.startswith("BLOCK:"):
                b_name = curr.split(":", 1)[1]
                cascade_of[b_name] = cid
                comp_members[cid].append(b_name)
            for nbr in adj_c[curr]:
                if nbr not in visited_c:
                    visited_c.add(nbr)
                    q.append(nbr)

    # Проход 2: привязка блоков, подключённых только между листьями
    for bid, c_id in list(cascade_of.items()):
        if len(comp_members[c_id]) == 1 and not adj_c[f"BLOCK:{bid}"]:
            cand_leaves = [lid for lid, nbrs in leaf_neighbors.items() if bid in nbrs and len(nbrs) > 1]
            cand_leaves.sort(key=lambda lid: len(leaf_neighbors[lid]))
            for lid in cand_leaves:
                for other_b in leaf_neighbors[lid]:
                    if other_b != bid and other_b in cascade_of:
                        cascade_of[bid] = cascade_of[other_b]
                        break
                if cascade_of[bid] != c_id:
                    break

    return cascade_of


def split_cross_cascade_leaves(blocks: dict, edges: list, hub_ids: set, block_hemi: dict):
    """
    Разделяет однопортовый листовой узел ТОЛЬКО если он соединяет разные полушария (WEST и EAST)
    или связывает более 2 независимых каскадов. Внутри одного каскада лист никогда не дробится.
    """
    cascade_of = find_cascades(blocks, edges, hub_ids)

    leaf_edges = defaultdict(list)
    for e in edges:
        for idx, end in enumerate((e["sources"][0], e["targets"][0])):
            bid, _ = end.split(".", 1)
            if bid in blocks and is_leaf(blocks[bid]):
                leaf_edges[bid].append((e, idx))

    for bid, connected in list(leaf_edges.items()):
        if len(connected) <= 1:
            continue

        by_group = defaultdict(list)
        hemis_seen = set()
        for e, idx in connected:
            other_end = e["targets"][0] if idx == 0 else e["sources"][0]
            nbid, npid = other_end.split(".", 1)
            if nbid in hub_ids:
                hemi = blocks[nbid]["pin_side"].get(npid, "EAST")
                casc = f"HUB_{other_end}"
            else:
                hemi = block_hemi.get(nbid) if block_hemi.get(nbid) in ("WEST", "EAST") else "EAST"
                casc = cascade_of.get(nbid, nbid)
            hemis_seen.add(hemi)
            by_group[(hemi, casc)].append((e, idx))

        need_split = (len(hemis_seen) > 1) or (len(by_group) > 2)
        if not need_split or len(by_group) <= 1:
            if len(hemis_seen) == 1:
                block_hemi[bid] = next(iter(hemis_seen))
            continue

        orig = blocks[bid]
        pin_id, pin_label = (orig["west"] + orig["east"])[0]
        side = orig["pin_side"][pin_id]

        for clone_idx, ((hemi, _), edge_list) in enumerate(by_group.items(), 1):
            if clone_idx == 1:
                block_hemi[bid] = hemi
                continue
            suffix = clone_idx
            new_id = f"{bid}_{suffix}"
            while new_id in blocks:
                suffix += 1
                new_id = f"{bid}_{suffix}"

            blocks[new_id] = {
                "id": new_id,
                "title": orig["title"],
                "subtitle": orig["subtitle"],
                "theme": orig["theme"],
                "west": [(pin_id, pin_label)] if side == "WEST" else [],
                "east": [(pin_id, pin_label)] if side == "EAST" else [],
                "pin_side": {pin_id: side},
            }
            block_hemi[new_id] = hemi
            for e, idx in edge_list:
                if idx == 0:
                    e["sources"][0] = f"{new_id}.{pin_id}"
                else:
                    e["targets"][0] = f"{new_id}.{pin_id}"


def optimize_graph_topology(blocks: dict, edges: list):
    """
    Математическая оптимизация ортогонального графа с двусторонними портами (WEST / EAST):
    1. Поиск центральных хабов в каждой компоненте связности графа.
    2. Ориентация левого (WEST) и правого (EAST) полушарий.
    3. Разделение глобальных листьев между полушариями и каскадами.
    4. Ориентация однопортовых листьев и вторичных двухполюсников.
    5. BFS-развязка односторонних петель (WEST-WEST / EAST-EAST) с ранжированием кандидатов.
    6. Нормализация направления рёбер (EAST -> WEST + учёт глубины BFS) и фильтрация дубликатов.
    """
    if not blocks or not edges:
        return

    def build_adj():
        p_adj = defaultdict(list)
        b_adj = defaultdict(set)
        b_deg = defaultdict(int)
        for e in edges:
            s, t = e["sources"][0], e["targets"][0]
            p_adj[s].append(t)
            p_adj[t].append(s)
            sb, tb = s.split(".", 1)[0], t.split(".", 1)[0]
            b_adj[sb].add(tb)
            b_adj[tb].add(sb)
            b_deg[sb] += 1
            b_deg[tb] += 1
        return p_adj, b_adj, b_deg

    adj, block_adj, block_deg = build_adj()

    def flip_block(b):
        b["west"], b["east"] = b["east"], b["west"]
        rebuild_pin_side(b)

    def set_two_port_sides(b, pin_a, side_a, pin_b, side_b):
        if pin_a == pin_b:
            return
        all_pins = dict(b["west"] + b["east"])
        if pin_a not in all_pins or pin_b not in all_pins:
            return
        b["west"] = []
        b["east"] = []
        for pid, s in ((pin_a, side_a), (pin_b, side_b)):
            b[s.lower()].append((pid, all_pins[pid]))
        rebuild_pin_side(b)

    def move_port_to_side(b, pid, target_side):
        if b["pin_side"].get(pid) == target_side:
            return
        src_list = b["west"] if target_side == "EAST" else b["east"]
        dst_list = b["east"] if target_side == "EAST" else b["west"]
        for i, (p, lbl) in enumerate(src_list):
            if p == pid:
                src_list.pop(i)
                dst_list.append((p, lbl))
                break
        rebuild_pin_side(b)

    def get_side(port):
        bid, pid = port.split(".", 1)
        return blocks.get(bid, {}).get("pin_side", {}).get(pid)

    # 1. Находим главный хаб в каждой компоненте связности графа
    comp_visited = set()
    hub_ids = []
    for start_bid in blocks:
        if start_bid in comp_visited:
            continue
        comp = []
        q = deque([start_bid])
        comp_visited.add(start_bid)
        while q:
            curr = q.popleft()
            comp.append(curr)
            for nbr in block_adj[curr]:
                if nbr not in comp_visited:
                    comp_visited.add(nbr)
                    q.append(nbr)

        two_sided = [
            bid for bid in comp
            if blocks[bid]["west"] and blocks[bid]["east"] and not is_two_port(blocks[bid])
        ]
        if not two_sided:
            two_sided = [bid for bid in comp if not is_leaf(blocks[bid])]
        if two_sided:
            hub_ids.append(max(two_sided, key=lambda x: block_deg[x]))

    hub_set = set(hub_ids)
    primary_visited = set(hub_ids)
    block_hemi = {hid: "CENTER" for hid in hub_ids}
    bfs_depth = {hid: 0 for hid in hub_ids}
    queue = deque()

    def get_connection_hemi(nbid, npid):
        if nbid in hub_set:
            return blocks[nbid]["pin_side"].get(npid, "EAST")
        h = block_hemi.get(nbid)
        return h if h in ("WEST", "EAST") else None

    for hid in hub_ids:
        for pid, side in blocks[hid]["pin_side"].items():
            port = f"{hid}.{pid}"
            for nbr_port in adj[port]:
                nbid, npid = nbr_port.split(".", 1)
                if nbid not in primary_visited and not is_leaf(blocks[nbid]):
                    queue.append((nbid, npid, side, 1))

    def orient_node(bid, entry_pin, hemi, visited_set):
        b = blocks[bid]
        expected_entry = "EAST" if hemi == "WEST" else "WEST"
        expected_exit = "WEST" if hemi == "WEST" else "EAST"

        if is_two_port(b):
            pins = [p[0] for p in (b["west"] + b["east"])]
            other_pin = pins[1] if pins[0] == entry_pin else pins[0]

            entry_nbr_sides = {
                get_side(np) for np in adj[f"{bid}.{entry_pin}"]
                if np.split(".", 1)[0] in visited_set and not is_leaf(blocks[np.split(".", 1)[0]])
            }
            other_nbr_sides = {
                get_side(np) for np in adj[f"{bid}.{other_pin}"]
                if np.split(".", 1)[0] in visited_set and not is_leaf(blocks[np.split(".", 1)[0]])
            }
            if expected_exit in entry_nbr_sides and expected_entry in other_nbr_sides and expected_entry not in entry_nbr_sides:
                set_two_port_sides(b, other_pin, expected_entry, entry_pin, expected_exit)
                return

            if is_passthrough(b):
                set_two_port_sides(b, entry_pin, expected_entry, other_pin, expected_exit)
            else:
                other_has_downstream = any(
                    nbr_p.split(".", 1)[0] not in visited_set or is_leaf(blocks[nbr_p.split(".", 1)[0]])
                    for nbr_p in adj[f"{bid}.{other_pin}"]
                )
                if other_has_downstream:
                    set_two_port_sides(b, entry_pin, expected_entry, other_pin, expected_exit)
                else:
                    set_two_port_sides(b, entry_pin, expected_entry, other_pin, expected_entry)
        else:
            if not b["west"] or not b["east"]:
                up_pins = []
                down_pins = []
                for pid in list(b["pin_side"].keys()):
                    nbrs = adj[f"{bid}.{pid}"]
                    has_up = any(np.split(".", 1)[0] in visited_set and not is_leaf(blocks[np.split(".", 1)[0]]) for np in nbrs)
                    has_down = any(np.split(".", 1)[0] not in visited_set or is_leaf(blocks[np.split(".", 1)[0]]) for np in nbrs)
                    if has_up and not has_down:
                        up_pins.append(pid)
                    elif has_down:
                        down_pins.append(pid)
                if up_pins and down_pins:
                    for pid in up_pins:
                        move_port_to_side(b, pid, expected_entry)
                    for pid in down_pins:
                        move_port_to_side(b, pid, expected_exit)
                    return

            match_votes = 0
            mismatch_votes = 0
            for pid, pside in b["pin_side"].items():
                port = f"{bid}.{pid}"
                for nbr_port in adj[port]:
                    nbid = nbr_port.split(".", 1)[0]
                    if nbid in visited_set and not is_leaf(blocks[nbid]):
                        if pside == expected_entry:
                            match_votes += 1
                        else:
                            mismatch_votes += 1
            if mismatch_votes > match_votes:
                flip_block(b)

    # 2. Основной обход функциональных блоков от хабов
    while queue:
        bid, entry_pin, hemi, depth = queue.popleft()
        if bid in primary_visited:
            continue
        orient_node(bid, entry_pin, hemi, primary_visited)
        primary_visited.add(bid)
        block_hemi[bid] = hemi
        bfs_depth[bid] = depth

        expected_entry = "EAST" if hemi == "WEST" else "WEST"
        ordered_pins = sorted(
            blocks[bid]["pin_side"].keys(),
            key=lambda p: 0 if blocks[bid]["pin_side"][p] == expected_entry else 1
        )
        for pid in ordered_pins:
            port = f"{bid}.{pid}"
            for nbr_port in adj[port]:
                nbid, npid = nbr_port.split(".", 1)
                if nbid not in primary_visited and not is_leaf(blocks[nbid]):
                    queue.append((nbid, npid, hemi, depth + 1))

    # Определяем полушарие для двухполюсников, подключённых только между листовыми узлами
    secondary_nodes = []
    for bid, b in blocks.items():
        if bid not in primary_visited and not is_leaf(b):
            inferred_hemi = "EAST"
            inferred_depth = 2
            for nbr in block_adj[bid]:
                if nbr in block_hemi and block_hemi[nbr] in ("WEST", "EAST"):
                    inferred_hemi = block_hemi[nbr]
                    inferred_depth = bfs_depth.get(nbr, 1) + 1
                    break
                for nbr2 in block_adj[nbr]:
                    if nbr2 in block_hemi and block_hemi[nbr2] in ("WEST", "EAST"):
                        inferred_hemi = block_hemi[nbr2]
                        inferred_depth = bfs_depth.get(nbr2, 1) + 1
                        break
            block_hemi[bid] = inferred_hemi
            bfs_depth[bid] = inferred_depth
            secondary_nodes.append(bid)

    # 3. Разделение глобальных листьев между полушариями и каскадами
    split_cross_cascade_leaves(blocks, edges, hub_set, block_hemi)
    adj, block_adj, block_deg = build_adj()

    # 4. Ориентация однопортовых листьев с приоритетом основных блоков каскада (primary_visited)
    for bid, b in blocks.items():
        if is_leaf(b) and b["pin_side"]:
            primary_sides = []
            all_sides = []
            max_nbr_depth = 1
            inferred_leaf_hemi = block_hemi.get(bid) if block_hemi.get(bid) in ("WEST", "EAST") else None
            for pid in b["pin_side"]:
                port = f"{bid}.{pid}"
                for nbr_port in adj[port]:
                    nbid, npid = nbr_port.split(".", 1)
                    if not is_leaf(blocks[nbid]):
                        ps = blocks[nbid]["pin_side"].get(npid)
                        all_sides.append(ps)
                        max_nbr_depth = max(max_nbr_depth, bfs_depth.get(nbid, 1))
                        if nbid in primary_visited:
                            primary_sides.append(ps)
                        if not inferred_leaf_hemi:
                            inferred_leaf_hemi = get_connection_hemi(nbid, npid)

            block_hemi[bid] = inferred_leaf_hemi or "EAST"
            bfs_depth[bid] = max_nbr_depth + 1
            use_sides = primary_sides if primary_sides else all_sides
            if use_sides:
                east_count = sum(1 for s in use_sides if s == "EAST")
                west_count = sum(1 for s in use_sides if s == "WEST")
                if east_count > west_count:
                    desired_side = "WEST"
                elif west_count > east_count:
                    desired_side = "EAST"
                else:
                    desired_side = "EAST" if block_hemi[bid] == "WEST" else "WEST"
                current_side = next(iter(b["pin_side"].values()))
                if current_side != desired_side:
                    flip_block(b)

    # Ориентация вторичных двухполюсников (между уже сориентированными листьями/узлами)
    for bid in secondary_nodes:
        b = blocks[bid]
        if is_two_port(b):
            pins = [p[0] for p in (b["west"] + b["east"])]
            p0, p1 = pins[0], pins[1]
            p0_nbr_sides = [get_side(np) for np in adj[f"{bid}.{p0}"]]
            p1_nbr_sides = [get_side(np) for np in adj[f"{bid}.{p1}"]]
            if "EAST" in p0_nbr_sides or "WEST" in p1_nbr_sides:
                set_two_port_sides(b, p0, "WEST", p1, "EAST")
            elif "WEST" in p0_nbr_sides or "EAST" in p1_nbr_sides:
                set_two_port_sides(b, p0, "EAST", p1, "WEST")
            else:
                set_two_port_sides(b, p0, "WEST", p1, "EAST")

    # 5. BFS-развязка односторонних петель (WEST-WEST и EAST-EAST) с ранжированием кандидатов
    port_adj = defaultdict(list)
    for e in edges:
        u, v = e["sources"][0], e["targets"][0]
        port_adj[u].append(v)
        port_adj[v].append(u)

    dist = {}
    opp_hubs = defaultdict(list)
    bfs_q = deque()

    for e in edges:
        u, v = e["sources"][0], e["targets"][0]
        su, sv = get_side(u), get_side(v)
        if su and sv and su != sv:
            if v not in opp_hubs[u]:
                opp_hubs[u].append(v)
            if u not in opp_hubs[v]:
                opp_hubs[v].append(u)
            if u not in dist:
                dist[u] = 0
                bfs_q.append(u)
            if v not in dist:
                dist[v] = 0
                bfs_q.append(v)

    while bfs_q:
        curr = bfs_q.popleft()
        for nbr in port_adj[curr]:
            if get_side(nbr) == get_side(curr) and nbr not in dist:
                dist[nbr] = dist[curr] + 1
                opp_hubs[nbr] = list(opp_hubs[curr])
                bfs_q.append(nbr)

    def hub_priority(cand_port):
        cbid = cand_port.split(".", 1)[0]
        is_hub_or_leaf = 1 if (cbid in hub_set or is_leaf(blocks[cbid])) else 0
        return (is_hub_or_leaf, len(port_adj[cand_port]))

    def pick_valid_hub(candidates, target_port):
        t_block = target_port.split(".", 1)[0]
        valid = [c for c in candidates if c.split(".", 1)[0] != t_block]
        if not valid:
            return None
        valid.sort(key=hub_priority, reverse=True)
        return valid[0]

    for e in edges:
        u, v = e["sources"][0], e["targets"][0]
        su, sv = get_side(u), get_side(v)
        if su and sv and su == sv and u in dist and v in dist:
            if dist[u] < dist[v]:
                hub_p = pick_valid_hub(opp_hubs[u], v)
                if hub_p:
                    e["sources"][0] = hub_p
                    e["targets"][0] = v
            elif dist[v] < dist[u]:
                hub_p = pick_valid_hub(opp_hubs[v], u)
                if hub_p:
                    e["sources"][0] = hub_p
                    e["targets"][0] = u
            else:
                hub_u = pick_valid_hub(opp_hubs[u], v)
                hub_v = pick_valid_hub(opp_hubs[v], u)
                if hub_u:
                    e["sources"][0] = hub_u
                    e["targets"][0] = v
                elif hub_v:
                    e["sources"][0] = hub_v
                    e["targets"][0] = u

    # 6. Нормализация направления рёбер (EAST -> WEST + учёт глубины BFS) и фильтрация дубликатов
    deduped_edges = []
    seen_pairs = {}
    for e in edges:
        src, dst = e["sources"][0], e["targets"][0]
        sb, db = src.split(".", 1)[0], dst.split(".", 1)[0]
        if src == dst or sb == db:
            continue
        sside, dside = get_side(src), get_side(dst)
        if sside == "WEST" and dside == "EAST":
            src, dst = dst, src
            e["sources"], e["targets"] = [src], [dst]
        elif sside == dside:
            hemi = block_hemi.get(sb) if block_hemi.get(sb) in ("WEST", "EAST") else block_hemi.get(db, "EAST")
            ds, dd = bfs_depth.get(sb, 0), bfs_depth.get(db, 0)
            if (hemi == "WEST" and ds < dd) or (hemi != "WEST" and ds > dd):
                src, dst = dst, src
                e["sources"], e["targets"] = [src], [dst]

        pair_key = (src, dst)
        rev_key = (dst, src)
        if pair_key in seen_pairs or rev_key in seen_pairs:
            existing = seen_pairs.get(pair_key) or seen_pairs.get(rev_key)
            if not existing["layoutOptions"].get("elk.edge.label") and e["layoutOptions"].get("elk.edge.label"):
                existing["layoutOptions"]["elk.edge.label"] = e["layoutOptions"]["elk.edge.label"]
            continue
        seen_pairs[pair_key] = e
        deduped_edges.append(e)

    edges[:] = deduped_edges


def resolve_port_ref(raw_token: str, blocks: dict) -> str:
    """
    Разрешение ссылки 'блок.порт' по принципу самого длинного совпадения префикса блока
    (longest prefix match), устойчивое к точкам внутри исходных имён блоков и портов.
    """
    dot_positions = [i for i, ch in enumerate(raw_token) if ch == "."]
    fallback = None
    for pos in reversed(dot_positions):
        cand_bid = sanitize_id(raw_token[:pos])
        cand_pid = sanitize_id(raw_token[pos + 1:])
        if cand_bid in blocks:
            if cand_pid in blocks[cand_bid]["pin_side"]:
                return f"{cand_bid}.{cand_pid}"
            if fallback is None:
                fallback = f"{cand_bid}.{cand_pid}"
    if fallback:
        return fallback
    sb, sp = raw_token.split(".", 1)
    return f"{sanitize_id(sb)}.{sanitize_id(sp)}"


def parse_connect_statement(rest: str, lineno: int, warnings: list):
    """Разбор инструкции CONNECT с поддержкой кавычек любого типа и валидацией цвета."""
    label = ""
    m = re.search(r'"([^"]*)"|\'([^\']*)\'', rest)
    if m:
        label = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
        rest_clean = (rest[:m.start()] + " " + rest[m.end():]).strip()
    else:
        rest_clean = rest.strip()

    parts = rest_clean.split()
    if len(parts) < 2:
        warnings.append(f"Строка {lineno}: неверный формат CONNECT")
        return None

    raw_src, raw_dst = parts[0], parts[1]
    for tok in (raw_src, raw_dst):
        if "." not in tok or tok.startswith(".") or tok.endswith("."):
            warnings.append(f"Строка {lineno}: некорректный формат блок.порт -> '{tok}'")
            return None

    color = "blue"
    if len(parts) >= 3:
        cand = parts[2].lower()
        if is_valid_color(cand):
            color = cand
            if len(parts) > 3 and not label:
                label = " ".join(parts[3:])
                warnings.append(f"Строка {lineno}: подпись '{label}' без кавычек распознана автоматически")
        else:
            if not label:
                label = " ".join(parts[2:])
                warnings.append(f"Строка {lineno}: '{parts[2]}' не является цветом, интерпретировано как подпись '{label}'")
            else:
                warnings.append(f"Строка {lineno}: неизвестный цвет линии '{cand}', заменён на 'blue'")

    return raw_src, raw_dst, color, label


def parse_instructions(text: str, warnings_out=None):
    diagram_title = "Python + elkjs (ELK Engine)"
    blocks = {}
    raw_edges = []
    current = None
    warnings = []

    for lineno, raw in enumerate(text.splitlines(), 1):
        if raw.strip().startswith("```"):
            continue
        line = strip_inline_comment(raw)
        if not line:
            continue

        u_side = line.rstrip(":").strip().upper()
        if u_side in ("WEST", "EAST"):
            if current:
                current["_side"] = u_side
            else:
                warnings.append(f"Строка {lineno}: секция {u_side} объявлена вне блока BLOCK")
            continue

        kw_match = KEYWORD_RE.match(line)
        if kw_match:
            kw = kw_match.group(1).upper()
            arg = kw_match.group(2).strip()

            if kw == "DIAGRAM":
                diagram_title = strip_outer_quotes(arg) or diagram_title
                continue

            if kw == "BLOCK":
                if current:
                    blocks[current["id"]] = current
                raw_bid = strip_outer_quotes(arg)
                if not raw_bid:
                    warnings.append(f"Строка {lineno}: пустой идентификатор BLOCK")
                    current = None
                    continue
                bid = sanitize_id(raw_bid)
                if bid != raw_bid:
                    warnings.append(f"Строка {lineno}: ID блока '{raw_bid}' нормализован в '{bid}'")
                if bid in blocks:
                    warnings.append(f"Строка {lineno}: блок '{bid}' объявлен повторно — порты будут объединены")
                    current = blocks[bid]
                else:
                    current = {
                        "id": bid,
                        "title": raw_bid,
                        "subtitle": "",
                        "theme": "teal",
                        "west": [],
                        "east": [],
                        "pin_side": {},
                    }
                continue

            if kw == "TITLE" and current:
                current["title"] = strip_outer_quotes(arg) or current["title"]
                continue

            if kw == "SUBTITLE" and current:
                current["subtitle"] = strip_outer_quotes(arg)
                continue

            if kw in ("THEME", "COLOR") and current:
                val = strip_outer_quotes(arg).lower()
                if not is_valid_color(val):
                    warnings.append(f"Строка {lineno}: неизвестная тема '{val}', использована 'teal'")
                    val = "teal"
                current["theme"] = val
                continue

            if kw == "CONNECT":
                if current:
                    blocks[current["id"]] = current
                    current = None
                parsed = parse_connect_statement(arg, lineno, warnings)
                if parsed:
                    raw_edges.append((lineno, *parsed))
                continue

        if "|" in line and current and current.get("_side"):
            raw_pid, label = [x.strip() for x in line.split("|", 1)]
            if not raw_pid:
                warnings.append(f"Строка {lineno}: пустой идентификатор порта")
                continue
            pin_id = sanitize_id(raw_pid)
            if pin_id != raw_pid:
                warnings.append(f"Строка {lineno}: ID порта '{raw_pid}' нормализован в '{pin_id}'")
            side = current["_side"]
            if pin_id in current["pin_side"]:
                warnings.append(f"Строка {lineno}: дубликат порта '{pin_id}' в блоке '{current['id']}'")
                continue
            current[side.lower()].append((pin_id, strip_outer_quotes(label)))
            current["pin_side"][pin_id] = side
            continue

        warnings.append(f"Строка {lineno}: неизвестная инструкция -> {line}")

    if current:
        blocks[current["id"]] = current

    if not blocks:
        warnings.append("В файле инструкций не найдено ни одного блока BLOCK.")

    # Разрешение и валидация ссылок на блоки и порты после сбора всех BLOCK
    edges = []
    edge_counter = 0
    for lineno, raw_src, raw_dst, color, label in raw_edges:
        src = resolve_port_ref(raw_src, blocks)
        dst = resolve_port_ref(raw_dst, blocks)
        ok = True
        for end in (src, dst):
            bid, pid = end.split(".", 1)
            if bid not in blocks:
                warnings.append(f"Строка {lineno}: блок '{bid}' не найден (порт {end}) — связь пропущена")
                ok = False
            elif pid not in blocks[bid]["pin_side"]:
                warnings.append(f"Строка {lineno}: порт '{pid}' не найден в блоке '{bid}' — связь пропущена")
                ok = False
        if ok:
            edge_counter += 1
            edges.append({
                "id": f"e{edge_counter}",
                "sources": [src],
                "targets": [dst],
                "layoutOptions": {
                    "elk.edge.color": color,
                    "elk.edge.label": label,
                },
            })

    # Математическая оптимизация топологии графа
    optimize_graph_topology(blocks, edges)

    # Формирование узлов и портов для ELK
    elk_children = []
    for b in blocks.values():
        west = b.get("west", [])
        east = b.get("east", [])
        max_rows = max(len(west), len(east), 1)
        height = HEADER_H + max_rows * ROW_H + BOTTOM_PAD
        ports = []

        for i, (pid, label) in enumerate(west):
            py = HEADER_H + i * ROW_H + ROW_H / 2.0
            ports.append({
                "id": f"{b['id']}.{pid}",
                "pin_id": pid,
                "label": label,
                "side": "WEST",
                "x": 0,
                "y": py,
                "layoutOptions": {
                    "elk.port.side": "WEST",
                    "elk.port.index": str(i),
                },
            })

        for i, (pid, label) in enumerate(east):
            py = HEADER_H + i * ROW_H + ROW_H / 2.0
            ports.append({
                "id": f"{b['id']}.{pid}",
                "pin_id": pid,
                "label": label,
                "side": "EAST",
                "x": 200,
                "y": py,
                "layoutOptions": {
                    "elk.port.side": "EAST",
                    "elk.port.index": str(i),
                },
            })

        elk_children.append({
            "id": b["id"],
            "title": b["title"],
            "subtitle": b["subtitle"],
            "theme": b["theme"],
            "width": 200,
            "height": height,
            "ports": ports,
            "layoutOptions": {"elk.portConstraints": "FIXED_POS"},
        })

    if warnings_out is not None:
        warnings_out.extend(warnings)

    if warnings:
        print("Предупреждения при разборе инструкций:")
        for w in warnings:
            print("  -", w)

    return {
        "id": "root",
        "diagramTitle": diagram_title,
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT",
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.spacing.nodeNode": "34",
            "elk.layered.spacing.nodeNodeBetweenLayers": "114",
            "elk.spacing.edgeNode": "22",
            "elk.spacing.edgeEdge": "15",
            "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
            "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
            "elk.padding": "[top=70,left=70,bottom=70,right=70]",
        },
        "children": elk_children,
        "edges": edges,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Python + elkjs (ELK Engine)</title>
<script src="https://cdn.jsdelivr.net/npm/elkjs@0.9.3/lib/elk.bundled.js"></script>
<style>
  body{margin:0;font-family:'Segoe UI',Roboto,Arial,sans-serif;background:#0f172a;color:#f8fafc;height:100vh;display:flex;flex-direction:column;user-select:none}
  header{background:#1e293b;padding:10px 20px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #334155;z-index:10}
  .controls{display:flex;gap:8px;align-items:center}
  button{background:#2563eb;color:#fff;border:none;padding:7px 13px;border-radius:6px;cursor:pointer;font-weight:600;transition:background .15s}
  button:hover{background:#1d4ed8}
  button.download{background:#059669}
  button.download:hover{background:#047857}
  #canvas{flex:1;overflow:auto;background:#f8fafc;cursor:grab;position:relative}
  #canvas.dragging{cursor:grabbing}
  /* Безопасное центрирование без ухода в отрицательные координаты при увеличении */
  #mount{display:flex;min-width:100%;min-height:100%;width:max-content;height:max-content;padding:32px;box-sizing:border-box}
  #svg{margin:auto;display:block;flex-shrink:0;box-shadow:0 4px 20px rgba(15,23,42,0.08);border-radius:6px}
  #status{color:#38bdf8;font-size:13px;margin-right:10px}
  svg.has-hover .edge-group{opacity:0.18;transition:opacity .15s}
  svg.has-hover .edge-group.active{opacity:1}
  svg.has-hover .edge-group.active path.wire{stroke-width:3.5px}
  .edge-label{pointer-events:none}
  svg.has-hover .edge-label{opacity:0.18;transition:opacity .15s}
  svg.has-hover .edge-label.active{opacity:1}
  .edge-group{cursor:pointer}
</style>
</head>
<body>
<header>
  <div>
    <strong id="doc-title">Python + elkjs (ELK Engine)</strong>
    <div style="font-size:12px;color:#94a3b8">Ортогональная трассировка графа (колесо мыши — масштаб к курсору, зажатая ЛКМ — перемещение, наведение — подсветка связей)</div>
  </div>
  <div class="controls">
    <span id="status">Подготовка...</span>
    <button onclick="zoom(1.25)" title="Приблизить">+</button>
    <button onclick="zoom(0.8)" title="Отдалить">−</button>
    <button onclick="fitToScreen()" title="Вписать схему в экран">Вписать</button>
    <button onclick="resetZoom()" title="Масштаб 100%">100%</button>
    <button class="download" onclick="downloadSVG()">Скачать SVG</button>
  </div>
</header>
<div id="canvas"><div id="mount"></div></div>
<script>
const graphData = __GRAPH__;

const THEMES = {
  dark:    {header:"#1e293b",stroke:"#0f172a",bg:"#ffffff",badge:"#38bdf8"},
  slate:   {header:"#334155",stroke:"#1e293b",bg:"#f8fafc",badge:"#cbd5e1"},
  gray:    {header:"#374151",stroke:"#1f2937",bg:"#f3f4f6",badge:"#d1d5db"},
  red:     {header:"#dc2626",stroke:"#b91c1c",bg:"#fef2f2",badge:"#fecaca"},
  orange:  {header:"#c2410c",stroke:"#9a3412",bg:"#fff7ed",badge:"#fed7aa"},
  amber:   {header:"#b45309",stroke:"#92400e",bg:"#fffbeb",badge:"#fde68a"},
  emerald: {header:"#047857",stroke:"#065f46",bg:"#ecfdf5",badge:"#6ee7b7"},
  teal:    {header:"#0f766e",stroke:"#115e59",bg:"#ffffff",badge:"#5eead4"},
  sky:     {header:"#0284c7",stroke:"#0369a1",bg:"#f0f9ff",badge:"#bae6fd"},
  blue:    {header:"#1d4ed8",stroke:"#1e3a8a",bg:"#eff6ff",badge:"#93c5fd"},
  indigo:  {header:"#4338ca",stroke:"#3730a3",bg:"#eef2ff",badge:"#c7d2fe"},
  violet:  {header:"#6d28d9",stroke:"#5b21b6",bg:"#f5f3ff",badge:"#ddd6fe"},
  rose:    {header:"#be123c",stroke:"#9f1239",bg:"#fff1f2",badge:"#fecdd3"}
};

const LINE_COLORS = {
  red:"#dc2626", slate:"#334155", gray:"#64748b", orange:"#ea580c",
  amber:"#d97706", emerald:"#059669", teal:"#0d9488", sky:"#0284c7",
  blue:"#2563eb", indigo:"#4f46e5", violet:"#7c3aed", rose:"#e11d48",
  dark:"#0f172a"
};

function getTheme(t){
  if(!t) return THEMES.teal;
  if(THEMES[t]) return THEMES[t];
  if(t.startsWith("#")) return {header:t, stroke:t, bg:"#ffffff", badge:"#e2e8f0"};
  return THEMES.teal;
}

function getLineColor(c){
  if(!c) return LINE_COLORS.blue;
  if(LINE_COLORS[c]) return LINE_COLORS[c];
  if(c.startsWith("#")) return c;
  return LINE_COLORS.blue;
}

function esc(s){
  return String(s)
    .replace(/&/g,"&amp;")
    .replace(/</g,"&lt;")
    .replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;");
}

function measure(text, font="11px 'Segoe UI',Roboto,Arial,sans-serif"){
  const c = measure.c || (measure.c = document.createElement("canvas"));
  const ctx = c.getContext("2d");
  ctx.font = font;
  return ctx.measureText(text).width;
}

function clampSegmentCenter(p0, p1, isStart, isEnd, halfText){
  const x0 = p0.x, x1 = p1.x;
  let mid = (x0 + x1) / 2;
  if(isStart && isEnd) return mid;
  const len = Math.abs(x1 - x0);
  const maxShift = Math.max(0, (len / 2) - 6);
  const wantedPad = Math.min(halfText + 8, (len / 2) + Math.min(maxShift, 10));
  const dir = x1 >= x0 ? 1 : -1;
  if(isStart && !isEnd && Math.abs(mid - x0) < wantedPad) mid = x0 + dir * wantedPad;
  else if(isEnd && !isStart && Math.abs(x1 - mid) < wantedPad) mid = x1 - dir * wantedPad;
  return mid;
}

function boxesOverlap(a, b){
  return !(a.x2 < b.x1 || a.x1 > b.x2 || a.y2 < b.y1 || a.y1 > b.y2);
}

function adjustWidths(){
  for(const n of graphData.children){
    let ml=0, mr=0;
    for(const p of n.ports||[]){
      const w = measure(p.label || "", "600 11px 'Segoe UI',Roboto,Arial,sans-serif");
      if(p.side==="WEST") ml = Math.max(ml, w);
      else mr = Math.max(mr, w);
    }
    const tw = measure(n.title || "", "700 12.5px 'Segoe UI',Roboto,Arial,sans-serif");
    const sw = measure(n.subtitle || "", "500 10.2px 'Segoe UI',Roboto,Arial,sans-serif");
    const header = Math.max(tw, sw) + 28;
    const pins = ml + mr + 52;
    n.width = Math.ceil(Math.max(header, pins, 140));
    n._ml = ml;
    n._mr = mr;
    for(const p of n.ports||[]) if(p.side==="EAST") p.x = n.width;
  }
}

async function render(){
  if(graphData.diagramTitle){
    document.getElementById("doc-title").textContent = graphData.diagramTitle;
    document.title = graphData.diagramTitle + " — Python + elkjs (ELK Engine)";
  }
  if(!graphData.children || graphData.children.length === 0){
    document.getElementById("status").textContent = "Пустая схема (0 блоков)";
    return;
  }
  if(typeof ELK === "undefined"){
    throw new Error("Не удалось загрузить библиотеку elkjs (проверьте интернет)");
  }
  adjustWidths();
  const elk = new ELK();
  const laid = await elk.layout(graphData);
  const W = Math.max(400, Math.ceil(laid.width||2000));
  const H = Math.max(300, Math.ceil(laid.height||1400));
  const deg = {};
  for(const e of laid.edges||[]){
    for(const s of e.sources||[]) deg[s]=(deg[s]||0)+1;
    for(const t of e.targets||[]) deg[t]=(deg[t]||0)+1;
  }

  let o = [`<svg id="svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="background:#f8fafc;font-family:'Segoe UI',Roboto,Arial,sans-serif">`];
  o.push(`<defs><pattern id="g" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0L0 0 0 20" fill="none" stroke="#e2e8f0" stroke-width=".7"/></pattern><pattern id="G" width="100" height="100" patternUnits="userSpaceOnUse"><rect width="100" height="100" fill="url(#g)"/><path d="M100 0L0 0 0 100" fill="none" stroke="#cbd5e1" stroke-width="1.1"/></pattern><filter id="s"><feDropShadow dx="0" dy="2" stdDeviation="2.5" flood-opacity=".1"/></filter></defs><rect width="100%" height="100%" fill="url(#G)"/>`);

  let labelLayer = [];
  const placedLabels = [];
  for(const n of laid.children||[]){
    placedLabels.push({x1: n.x - 4, y1: n.y - 4, x2: n.x + n.width + 4, y2: n.y + n.height + 4});
  }

  o.push(`<g id="edges">`);
  for(const e of laid.edges||[]){
    const opts = e.layoutOptions || {};
    const colorKey = opts["elk.edge.color"] || "blue";
    const label = opts["elk.edge.label"] || "";
    const c = getLineColor(colorKey);
    const sPort = (e.sources||[])[0] || "";
    const tPort = (e.targets||[])[0] || "";
    const sNode = sPort.split(".")[0];
    const tNode = tPort.split(".")[0];

    o.push(`<g class="edge-group" data-edge="${esc(e.id)}" data-src="${esc(sNode)}" data-dst="${esc(tNode)}">`);
    for(const sec of e.sections||[]){
      const pts = [sec.startPoint, ...(sec.bendPoints||[]), sec.endPoint];
      const d = pts.map((p,i)=>`${i?'L':'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
      o.push(`<path d="${d}" fill="none" stroke="#f8fafc" stroke-width="6" stroke-linecap="round"/>`);
      o.push(`<path class="wire" d="${d}" fill="none" stroke="${c}" stroke-width="2.2" stroke-linecap="round"/>`);
      if(label){
        const lw = measure(label, "600 10px 'Segoe UI',Roboto,Arial,sans-serif");
        const candidates = [];
        for(let i=0;i<pts.length-1;i++){
          const dx=Math.abs(pts[i+1].x-pts[i].x), dy=Math.abs(pts[i+1].y-pts[i].y);
          const len=Math.hypot(dx, dy);
          if(len < 18) continue;
          const isH = (dx >= dy);
          const fits = len >= (lw + 12);
          const score = (fits ? 1000 : 0) + (isH && fits ? 300 : 0) + len;
          candidates.push({p0:pts[i], p1:pts[i+1], isH, len, idx:i, score});
        }
        candidates.sort((a,b) => b.score - a.score);

        const offsets = [-5, 14, -18, 27];
        let placed = false;
        for(const off of offsets){
          if(placed) break;
          for(const cand of candidates){
            if(cand.isH){
              const isStart = (cand.idx === 0);
              const isEnd = (cand.idx === pts.length - 2);
              const x = clampSegmentCenter(cand.p0, cand.p1, isStart, isEnd, lw / 2);
              const yWire = (cand.p0.y + cand.p1.y) / 2;
              const y = yWire + off;
              const box = {x1: x - lw/2 - 2, y1: y - 10, x2: x + lw/2 + 2, y2: y + 1};
              if(!placedLabels.some(b => boxesOverlap(box, b))){
                placedLabels.push(box);
                labelLayer.push(`<text class="edge-label" data-edge="${esc(e.id)}" data-src="${esc(sNode)}" data-dst="${esc(tNode)}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" font-size="10" font-weight="600" fill="${c}" stroke="#f8fafc" stroke-width="3.5" paint-order="stroke">${esc(label)}</text>`);
                placed = true;
                break;
              }
            } else {
              const xWire = (cand.p0.x + cand.p1.x) / 2;
              const x = xWire + off;
              const y = (cand.p0.y + cand.p1.y) / 2;
              const box = {x1: x - 10, y1: y - lw/2 - 2, x2: x + 1, y2: y + lw/2 + 2};
              if(!placedLabels.some(b => boxesOverlap(box, b))){
                placedLabels.push(box);
                labelLayer.push(`<text class="edge-label" data-edge="${esc(e.id)}" data-src="${esc(sNode)}" data-dst="${esc(tNode)}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" transform="rotate(-90 ${x.toFixed(1)} ${y.toFixed(1)})" text-anchor="middle" font-size="10" font-weight="600" fill="${c}" stroke="#f8fafc" stroke-width="3.5" paint-order="stroke">${esc(label)}</text>`);
                placed = true;
                break;
              }
            }
          }
        }
        if(!placed && candidates.length > 0){
          const cand = candidates[0];
          if(cand.isH){
            const x = clampSegmentCenter(cand.p0, cand.p1, cand.idx===0, cand.idx===pts.length-2, lw / 2);
            const y = (cand.p0.y + cand.p1.y)/2 - 5;
            labelLayer.push(`<text class="edge-label" data-edge="${esc(e.id)}" data-src="${esc(sNode)}" data-dst="${esc(tNode)}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" font-size="10" font-weight="600" fill="${c}" stroke="#f8fafc" stroke-width="3.5" paint-order="stroke">${esc(label)}</text>`);
          } else {
            const x = (cand.p0.x + cand.p1.x)/2 - 5, y = (cand.p0.y + cand.p1.y)/2;
            labelLayer.push(`<text class="edge-label" data-edge="${esc(e.id)}" data-src="${esc(sNode)}" data-dst="${esc(tNode)}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" transform="rotate(-90 ${x.toFixed(1)} ${y.toFixed(1)})" text-anchor="middle" font-size="10" font-weight="600" fill="${c}" stroke="#f8fafc" stroke-width="3.5" paint-order="stroke">${esc(label)}</text>`);
          }
        }
      }
    }
    for(const j of e.junctionPoints||[]){
      o.push(`<circle cx="${j.x.toFixed(1)}" cy="${j.y.toFixed(1)}" r="4.2" fill="${c}" stroke="#fff" stroke-width="1.2"/>`);
    }
    o.push(`</g>`);
  }
  o.push(`</g><g id="nodes">`);

  for(const n of laid.children||[]){
    const st = getTheme(n.theme);
    const w = n.width, h = n.height;
    o.push(`<g class="node-group" data-node="${esc(n.id)}" transform="translate(${n.x.toFixed(1)},${n.y.toFixed(1)})">`);
    o.push(`<rect width="${w}" height="${h}" rx="6" fill="${st.bg}" stroke="${st.stroke}" stroke-width="1.8" filter="url(#s)"/>`);
    o.push(`<path d="M0 6Q0 0 6 0H${w-6}Q${w} 0 ${w} 6V44H0Z" fill="${st.header}"/>`);
    o.push(`<text x="${(w/2).toFixed(1)}" y="19" text-anchor="middle" fill="#fff" font-size="12.5" font-weight="700">${esc(n.title||"")}</text>`);
    o.push(`<text x="${(w/2).toFixed(1)}" y="35" text-anchor="middle" fill="${st.badge}" font-size="10.2">${esc(n.subtitle||"")}</text>`);

    const wp = (n.ports||[]).filter(p=>p.side==="WEST");
    const ep = (n.ports||[]).filter(p=>p.side==="EAST");
    const rows = Math.max(wp.length, ep.length);
    for(let r=1; r<rows; r++) o.push(`<line x1="0" y1="${44+r*24}" x2="${w}" y2="${44+r*24}" stroke="#e2e8f0"/>`);

    if(wp.length && ep.length){
      const ml = n._ml || 0, mr = n._mr || 0;
      const splitX = Math.max(24, Math.min(w - 24, 14 + ml + (w - 28 - ml - mr) / 2));
      o.push(`<line x1="${splitX.toFixed(1)}" y1="44" x2="${splitX.toFixed(1)}" y2="${h}" stroke="#cbd5e1" stroke-dasharray="3,3"/>`);
    }

    for(const p of n.ports||[]){
      const d = deg[p.id] || 0;
      const col = d>0 ? "#0284c7" : "#94a3b8";
      if(p.side==="WEST"){
        o.push(`<rect x="-5" y="${(p.y-4).toFixed(1)}" width="10" height="8" rx="2" fill="${col}" stroke="#0f172a"/>`);
        o.push(`<text x="10" y="${(p.y+4).toFixed(1)}" font-size="11" font-weight="${d>0?600:400}" fill="${d>0?'#0f172a':'#64748b'}">${esc(p.label||"")}</text>`);
      }else{
        o.push(`<rect x="${(w-5).toFixed(1)}" y="${(p.y-4).toFixed(1)}" width="10" height="8" rx="2" fill="${col}" stroke="#0f172a"/>`);
        o.push(`<text x="${(w-10).toFixed(1)}" y="${(p.y+4).toFixed(1)}" text-anchor="end" font-size="11" font-weight="${d>0?600:400}" fill="${d>0?'#0f172a':'#64748b'}">${esc(p.label||"")}</text>`);
      }
      if(d>1) o.push(`<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4.8" fill="#dc2626" stroke="#fff"/>`);
    }
    o.push(`</g>`);
  }
  o.push(`</g><g id="edge-labels">${labelLayer.join("")}</g></svg>`);
  document.getElementById("mount").innerHTML = o.join("");
  document.getElementById("status").textContent = `${(laid.children||[]).length} блоков, ${(laid.edges||[]).length} связей`;
  bindInteractions();
  requestAnimationFrame(() => {
    if(W > canvasEl.clientWidth || H > canvasEl.clientHeight) fitToScreen();
  });
}

function bindInteractions(){
  const svg = document.getElementById("svg");
  if(!svg) return;
  const edges = svg.querySelectorAll(".edge-group");
  const labels = svg.querySelectorAll(".edge-label");
  edges.forEach(eg => {
    const eid = eg.getAttribute("data-edge");
    eg.addEventListener("mouseenter", () => {
      svg.classList.add("has-hover");
      eg.classList.add("active");
      labels.forEach(lb => { if(lb.getAttribute("data-edge")===eid) lb.classList.add("active"); });
    });
    eg.addEventListener("mouseleave", () => {
      svg.classList.remove("has-hover");
      eg.classList.remove("active");
      labels.forEach(lb => lb.classList.remove("active"));
    });
  });
  const nodes = svg.querySelectorAll(".node-group");
  nodes.forEach(ng => {
    const nid = ng.getAttribute("data-node");
    ng.addEventListener("mouseenter", () => {
      svg.classList.add("has-hover");
      edges.forEach(eg => {
        if(eg.getAttribute("data-src")===nid || eg.getAttribute("data-dst")===nid) eg.classList.add("active");
      });
      labels.forEach(lb => {
        if(lb.getAttribute("data-src")===nid || lb.getAttribute("data-dst")===nid) lb.classList.add("active");
      });
    });
    ng.addEventListener("mouseleave", () => {
      svg.classList.remove("has-hover");
      edges.forEach(eg => eg.classList.remove("active"));
      labels.forEach(lb => lb.classList.remove("active"));
    });
  });
}

let scale=1;
const canvasEl = document.getElementById("canvas");

function zoom(f, clientX, clientY){
  const s = document.getElementById("svg");
  if(!s) return;
  const cRect = canvasEl.getBoundingClientRect();
  const targetX = (clientX !== undefined) ? clientX : (cRect.left + canvasEl.clientWidth / 2);
  const targetY = (clientY !== undefined) ? clientY : (cRect.top + canvasEl.clientHeight / 2);

  const sBefore = s.getBoundingClientRect();
  const relX = sBefore.width > 0 ? (targetX - sBefore.left) / sBefore.width : 0.5;
  const relY = sBefore.height > 0 ? (targetY - sBefore.top) / sBefore.height : 0.5;

  scale = Math.max(0.15, Math.min(5, scale * f));
  apply();

  const sAfter = s.getBoundingClientRect();
  const newClientX = sAfter.left + relX * sAfter.width;
  const newClientY = sAfter.top + relY * sAfter.height;

  canvasEl.scrollLeft += (newClientX - targetX);
  canvasEl.scrollTop += (newClientY - targetY);
}

function resetZoom(){
  const s = document.getElementById("svg");
  if(!s) return;
  scale = 1;
  apply();
  const sRect = s.getBoundingClientRect();
  const cRect = canvasEl.getBoundingClientRect();
  canvasEl.scrollLeft += (sRect.left + sRect.width / 2) - (cRect.left + canvasEl.clientWidth / 2);
  canvasEl.scrollTop += (sRect.top + sRect.height / 2) - (cRect.top + canvasEl.clientHeight / 2);
}

function fitToScreen(){
  const s = document.getElementById("svg");
  if(!s || !canvasEl.clientWidth || !canvasEl.clientHeight) return;
  const vb = s.viewBox.baseVal;
  if(!vb.width || !vb.height) return;
  const availW = Math.max(100, canvasEl.clientWidth - 64);
  const availH = Math.max(100, canvasEl.clientHeight - 64);
  scale = Math.max(0.15, Math.min(2, Math.min(availW / vb.width, availH / vb.height)));
  apply();
  canvasEl.scrollLeft = 0;
  canvasEl.scrollTop = 0;
}

function apply(){
  const s = document.getElementById("svg");
  if(!s) return;
  const vb = s.viewBox.baseVal;
  s.setAttribute("width", (vb.width * scale).toFixed(1));
  s.setAttribute("height", (vb.height * scale).toFixed(1));
}

let isDown=false, startX=0, startY=0, scrollL=0, scrollT=0;
canvasEl.addEventListener("mousedown", e => {
  if(e.button !== 0) return;
  isDown = true;
  canvasEl.classList.add("dragging");
  startX = e.clientX;
  startY = e.clientY;
  scrollL = canvasEl.scrollLeft;
  scrollT = canvasEl.scrollTop;
});
window.addEventListener("mouseup", () => {
  isDown = false;
  canvasEl.classList.remove("dragging");
});
window.addEventListener("mousemove", e => {
  if(!isDown) return;
  e.preventDefault();
  canvasEl.scrollLeft = scrollL - (e.clientX - startX);
  canvasEl.scrollTop = scrollT - (e.clientY - startY);
});
canvasEl.addEventListener("wheel", e => {
  e.preventDefault();
  zoom(e.deltaY < 0 ? 1.15 : 0.87, e.clientX, e.clientY);
}, {passive: false});

function downloadSVG(){
  const el=document.getElementById("svg");
  if(!el) return;
  const clone = el.cloneNode(true);
  clone.classList.remove("has-hover");
  const vb = el.viewBox.baseVal;
  clone.setAttribute("width", vb.width);
  clone.setAttribute("height", vb.height);
  let src=new XMLSerializer().serializeToString(clone);
  if(!src.includes('xmlns="http://www.w3.org/2000/svg"')) src=src.replace(/^<svg/,'<svg xmlns="http://www.w3.org/2000/svg"');
  src='<?xml version="1.0" standalone="no"?>\r\n'+src;
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([src],{type:"image/svg+xml"}));
  a.download="diagram.svg"; a.click();
}

render().catch(e=>{
  document.getElementById("status").textContent="Ошибка: "+e.message;
  console.error(e);
});
</script>
</body>
</html>
"""


class HelpOnErrorParser(argparse.ArgumentParser):
    """Парсер аргументов, выводящий полную инструкцию из шапки при любой ошибке вызова."""

    def print_help(self, file=None):
        out_stream = file if file is not None else sys.stdout
        print(USAGE_GUIDE, file=out_stream)

    def error(self, message):
        print(f"ОШИБКА АРГУМЕНТОВ: {message}\n", file=sys.stderr)
        print(USAGE_GUIDE, file=sys.stderr)
        sys.exit(1)


def build_arg_parser() -> HelpOnErrorParser:
    parser = HelpOnErrorParser(
        prog="shema_elk.py",
        add_help=False,
    )
    parser.add_argument(
        "-h", "--help",
        action="store_true",
        help="Показать полную справку и выйти",
    )
    parser.add_argument(
        "instructions",
        nargs="?",
        help="Путь к файлу с формальными DSL-инструкциями",
    )
    out_group = parser.add_mutually_exclusive_group()
    out_group.add_argument(
        "-o", "--output",
        dest="output",
        default=None,
        help="Путь к итоговому HTML-файлу или директории сохранения",
    )
    out_group.add_argument(
        "--beside",
        action="store_true",
        help="Сохранить shema_viewer.html рядом с входным файлом инструкций",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Не открывать итоговый HTML в браузере",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Завершаться с кодом 2 при любых предупреждениях, 0 блоках или 0 связях",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Только проверить DSL-файл без записи HTML",
    )
    return parser


def resolve_output_path(instr_path: Path, output_arg, beside: bool) -> Path:
    if output_arg:
        raw = str(output_arg)
        out = Path(raw)
        if raw.endswith(("/", "\\")) or (out.exists() and out.is_dir()):
            out = out / "shema_viewer.html"
        return out.resolve()
    if beside:
        return (instr_path.resolve().parent / "shema_viewer.html").resolve()
    return (Path.cwd() / "shema_viewer.html").resolve()


def should_open_browser(no_open: bool) -> bool:
    if no_open:
        return False
    browser_env = os.environ.get("BROWSER", "").strip().lower()
    if browser_env in ("true", "false", "none", "0", "/bin/true", "/usr/bin/true"):
        return False
    if os.environ.get("CI", "").strip().lower() in ("1", "true", "yes"):
        return False
    if os.name == "posix" and sys.platform != "darwin":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return False
    return True


def main():
    parser = build_arg_parser()

    if len(sys.argv) < 2:
        print("ОШИБКА: скрипт вызван без аргументов (не указан входной файл DSL).\n", file=sys.stderr)
        print(USAGE_GUIDE, file=sys.stderr)
        sys.exit(1)

    args = parser.parse_args()

    if args.help:
        print(USAGE_GUIDE)
        sys.exit(0)

    if not args.instructions:
        print("ОШИБКА: не указан обязательный путь к входному файлу DSL.\n", file=sys.stderr)
        print(USAGE_GUIDE, file=sys.stderr)
        sys.exit(1)

    instr_path = Path(args.instructions)
    if not instr_path.is_file():
        print(f"ОШИБКА: входной файл не найден -> {instr_path}\n", file=sys.stderr)
        print(USAGE_GUIDE, file=sys.stderr)
        sys.exit(1)

    try:
        instr = instr_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"ОШИБКА чтения файла '{instr_path}': {exc}", file=sys.stderr)
        sys.exit(1)

    warnings = []
    graph = parse_instructions(instr, warnings_out=warnings)
    n_blocks = len(graph["children"])
    n_edges = len(graph["edges"])
    n_warnings = len(warnings)

    print(f"Блоков: {n_blocks}, связей: {n_edges}, предупреждений: {n_warnings}")

    if n_blocks == 0:
        print("ОШИБКА: в файле инструкций не найдено ни одного валидного блока BLOCK.", file=sys.stderr)
        sys.exit(2)

    if args.strict and (n_warnings > 0 or n_edges == 0):
        print(
            "ОШИБКА (--strict): обнаружены предупреждения парсера или отсутствуют связи. "
            "HTML не записан; исправьте входной файл инструкций и повторите запуск.",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.check_only:
        print("Проверка успешно пройдена (--check-only, HTML не записывался).")
        sys.exit(0)

    safe_json = json.dumps(graph, ensure_ascii=False).replace("</script>", "<\\/script>")
    html = HTML_TEMPLATE.replace("__GRAPH__", safe_json)

    out = resolve_output_path(instr_path, args.output, args.beside)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
    except OSError as exc:
        print(f"ОШИБКА записи файла '{out}': {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Создан: {out}")

    if should_open_browser(args.no_open):
        try:
            webbrowser.open(out.as_uri())
        except Exception:
            pass


if __name__ == "__main__":
    main()
