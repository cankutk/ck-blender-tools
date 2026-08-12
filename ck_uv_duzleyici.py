# SPDX-FileCopyrightText: 2026 Cankut
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import bmesh
import random
from bpy.props import PointerProperty, FloatProperty, BoolProperty, IntProperty

bl_info = {
    "name": "CK_UV_TOOL",
    "author": "Cankut",
    "version": (1, 6),
    # bm.loops.layers.float_color (Color Attribute) API'si 3.2 ile geldi.
    "blender": (3, 2, 0),
    "location": "UV Editor > Sidebar > Custom Tools",
    "description": "Aktif yüzeye göre UV island'ı düzeltir, çoklu adacıkları eşitler ve "
                    "tile eden trim desteğiyle hotspot mapping yapar.",
    "doc_url": "https://github.com/cankutk/ck-blender-tools",
    "tracker_url": "https://github.com/cankutk/ck-blender-tools/issues",
    "category": "UV",
}


def ck_edit_mesh_poll(context):
    """Tüm operatörler bmesh.from_edit_mesh kullanıyor; edit mode dışında bu
    ValueError atar ve kullanıcı kırmızı bir Python hatası görür."""
    obj = context.active_object
    return context.mode == 'EDIT_MESH' and obj is not None and obj.type == 'MESH'


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

def ck_find_islands_by_topology(faces):
    """Verilen face listesini, gerçek mesh bağlantısına (ortak edge) göre adacıklara ayırır.
    Seam olarak işaretlenmiş edge'lerden geçiş yapılmaz.

    NOT: Eskiden bu işlev UV koordinatlarının çakışmasına bakarak adacık buluyordu.
    Ama hotspot atlas gibi hücreleri boşluksuz/grid halinde diziliyse (komşu hücrelerin
    sınır UV koordinatları tam çakışıyorsa) bu yöntem komşu hücreleri yanlışlıkla TEK
    bir dev adacık olarak birleştiriyordu. Seam flag'ine göre ayırmak hem bu sorunu
    çözüyor hem de senin zaten seam ile island/cell ayırma workflow'una birebir uyuyor.
    """
    face_set = set(faces)
    visited = set()
    islands = []

    # Gelen listenin sırasında dolaşıyoruz; set üzerinde iterasyon pointer hash'ine
    # bağlı olduğu için adacık sırası çalıştırmalar arasında değişir ve seed'li
    # rastgelelik tekrarlanabilir olmaz.
    for start_face in faces:
        if start_face in visited:
            continue
        island = []
        stack = [start_face]
        visited.add(start_face)

        while stack:
            curr_face = stack.pop()
            island.append(curr_face)
            for edge in curr_face.edges:
                if edge.seam:
                    continue
                for neighbor in edge.link_faces:
                    if neighbor in face_set and neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)

        islands.append(island)

    return islands


def ck_island_bounds(island, uv_layer):
    """Bir adacığın UV bounding box / aspect / alan bilgisini döndürür."""
    min_x, min_y = float('inf'), float('inf')
    max_x, max_y = float('-inf'), float('-inf')

    for face in island:
        for loop in face.loops:
            uv = loop[uv_layer].uv
            min_x = min(min_x, uv.x)
            max_x = max(max_x, uv.x)
            min_y = min(min_y, uv.y)
            max_y = max(max_y, uv.y)

    width = max(max_x - min_x, 0.000001)
    height = max(max_y - min_y, 0.000001)

    return {
        'min_x': min_x, 'max_x': max_x,
        'min_y': min_y, 'max_y': max_y,
        'width': width, 'height': height,
        'aspect': width / height,
        'area': width * height,
    }


def ck_get_color_source(bm):
    """Atlas mesh'inde renk katmanını bulur. Hem 'Face Corner' hem 'Vertex' domain
    Color Attribute'larını destekler (yeni ve eski tip renk katmanları dahil).
    Döndürür: (layer, domain) -- domain 'LOOP' veya 'VERT'. Bulunamazsa (None, None)."""
    layer = bm.loops.layers.float_color.active
    if layer is not None:
        return layer, 'LOOP'
    layer = bm.loops.layers.color.active
    if layer is not None:
        return layer, 'LOOP'
    layer = bm.verts.layers.float_color.active
    if layer is not None:
        return layer, 'VERT'
    layer = bm.verts.layers.color.active
    if layer is not None:
        return layer, 'VERT'
    return None, None


def ck_classify_cell(island, color_source):
    """Adacığın ortalama rengine bakarak TRIM mi HOTSPOT mu olduğuna karar verir.
    Kırmızı ağır basıyorsa TRIM (tile eden trim), aksi halde (renk yoksa dahil) HOTSPOT."""
    layer, domain = color_source
    if layer is None:
        return 'HOTSPOT'

    r_total = b_total = 0.0
    count = 0

    for face in island:
        if domain == 'LOOP':
            for loop in face.loops:
                c = loop[layer]
                r_total += c[0]
                b_total += c[2]
                count += 1
        else:  # 'VERT'
            for vert in face.verts:
                c = vert[layer]
                r_total += c[0]
                b_total += c[2]
                count += 1

    if count == 0:
        return 'HOTSPOT'

    r_avg = r_total / count
    b_avg = b_total / count

    if r_avg > b_avg and r_avg > 0.4:
        return 'TRIM'
    return 'HOTSPOT'


def ck_read_atlas_cells(atlas_obj):
    """Atlas (hotspot şablonu) objesinin UV adacıklarını hücre (cell) listesi olarak okur.
    Her hücreye, vertex/color attribute'a göre 'type' (HOTSPOT veya TRIM) atanır.
    Object mode'daki mesh datasını kullanır, edit mode'a girmeye gerek yoktur."""
    bm = bmesh.new()
    bm.from_mesh(atlas_obj.data)
    bm.faces.ensure_lookup_table()

    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None or len(bm.faces) == 0:
        bm.free()
        return None

    color_source = ck_get_color_source(bm)

    islands = ck_find_islands_by_topology(list(bm.faces))
    cells = []
    for island in islands:
        bounds = ck_island_bounds(island, uv_layer)
        bounds['type'] = ck_classify_cell(island, color_source)
        cells.append(bounds)

    bm.free()
    return cells


def ck_score_pool(scored, variation, rng=None):
    """Skorlanmış (score, ...) tuple listesinden en iyisini (ya da variation
    içindeki havuzdan rastgele birini) seçer. Ortak seçim mantığı."""
    if not scored:
        return None
    scored.sort(key=lambda s: s[0])
    best_score = scored[0][0]
    if variation <= 0.0:
        return scored[0]
    pool = [s for s in scored if s[0] <= best_score + variation]
    return (rng or random).choice(pool)


def ck_best_matching_hotspot_cell(hotspot_cells, bounds, variation=0.0, rng=None):
    """SADECE Hotspot hücreleri arasından en uygununu seçer (standart, sınırlı,
    aspect+alan bazlı stretch eşleştirme). Döndürür: (cell, flip)."""
    aspect = bounds['aspect']
    area = bounds['area']
    scored = []

    for cell in hotspot_cells:
        for flip, cell_aspect in ((False, cell['aspect']), (True, 1.0 / cell['aspect'])):
            aspect_diff = abs(cell_aspect - aspect) / max(aspect, cell_aspect, 0.000001)
            area_diff = abs(cell['area'] - area) / max(cell['area'], area, 0.000001)
            score = (aspect_diff * 2.0) + area_diff
            scored.append((score, cell, flip))

    chosen = ck_score_pool(scored, variation, rng)
    if chosen is None:
        return None, False
    return chosen[1], chosen[2]


def ck_best_matching_trim_cell(trim_cells, bounds, variation=0.0, rng=None):
    """SADECE Trim (sınırsız/tile eden) hücreleri arasından en uygununu seçer
    (yalnızca kısa kenar boyutuna göre -- uzun kenar zaten sınırsız olduğu için
    aspect anlamsız). Döndürür: (cell, flip)."""
    island_short = min(bounds['width'], bounds['height'])
    island_long_is_x = bounds['width'] >= bounds['height']
    scored = []

    for cell in trim_cells:
        cell_short = min(cell['width'], cell['height'])
        cell_long_is_x = cell['width'] >= cell['height']
        score = abs(cell_short - island_short) / max(cell_short, island_short, 0.000001)
        flip = island_long_is_x != cell_long_is_x
        scored.append((score, cell, flip))

    chosen = ck_score_pool(scored, variation, rng)
    if chosen is None:
        return None, False
    return chosen[1], chosen[2]


def ck_best_matching_cell_combined(hotspot_cells, trim_cells, bounds, variation=0.0, rng=None):
    """Hotspot ve Trim hücrelerini TEK havuzda değerlendirip en uygununu seçer
    (otomatik mod -- hangi tipin daha iyi eşleştiğine göre kendisi karar verir).

    Döndürür: (cell, flip, cell_type) -- cell_type 'HOTSPOT' veya 'TRIM'
    """
    hs_cell, hs_flip = ck_best_matching_hotspot_cell(hotspot_cells, bounds, 0.0, rng)
    tr_cell, tr_flip = ck_best_matching_trim_cell(trim_cells, bounds, 0.0, rng)

    aspect = bounds['aspect']
    area = bounds['area']
    island_short = min(bounds['width'], bounds['height'])

    scored = []
    if hs_cell is not None:
        cell_aspect = (1.0 / hs_cell['aspect']) if hs_flip else hs_cell['aspect']
        aspect_diff = abs(cell_aspect - aspect) / max(aspect, cell_aspect, 0.000001)
        area_diff = abs(hs_cell['area'] - area) / max(hs_cell['area'], area, 0.000001)
        score = (aspect_diff * 2.0) + area_diff
        scored.append((score, hs_cell, hs_flip, 'HOTSPOT'))
    if tr_cell is not None:
        cell_short = min(tr_cell['width'], tr_cell['height'])
        score = abs(cell_short - island_short) / max(cell_short, island_short, 0.000001)
        scored.append((score, tr_cell, tr_flip, 'TRIM'))

    if not scored:
        return None, False, None

    if variation <= 0.0:
        # Zaten yukarıda her havuzdan en iyisini aldık, aralarından en iyisini seç.
        scored.sort(key=lambda s: s[0])
        chosen = scored[0]
    else:
        # Variation'lı modda tüm adayları yeniden, tek havuzda skorlayıp seçelim.
        full_scored = []
        for cell in hotspot_cells:
            for flip, cell_aspect in ((False, cell['aspect']), (True, 1.0 / cell['aspect'])):
                aspect_diff = abs(cell_aspect - aspect) / max(aspect, cell_aspect, 0.000001)
                area_diff = abs(cell['area'] - area) / max(cell['area'], area, 0.000001)
                score = (aspect_diff * 2.0) + area_diff
                full_scored.append((score, cell, flip, 'HOTSPOT'))
        for cell in trim_cells:
            cell_short = min(cell['width'], cell['height'])
            cell_long_is_x = cell['width'] >= cell['height']
            island_long_is_x = bounds['width'] >= bounds['height']
            score = abs(cell_short - island_short) / max(cell_short, island_short, 0.000001)
            flip = island_long_is_x != cell_long_is_x
            full_scored.append((score, cell, flip, 'TRIM'))
        chosen = ck_score_pool(full_scored, variation, rng)

    if chosen is None:
        return None, False, None

    return chosen[1], chosen[2], chosen[3]


def ck_apply_cell_offset(cell, offset):
    """Hücreyi kenarlarından 'offset' kadar içeri çeker (eşleştirme bu fonksiyondan
    ETKİLENMEZ, sadece son sığdırma bu daraltılmış hücreye yapılır)."""
    if offset <= 0.0:
        return cell

    safe_x = max(0.0, min(offset, cell['width'] / 2.0 - 0.000001))
    safe_y = max(0.0, min(offset, cell['height'] / 2.0 - 0.000001))

    new_width = cell['width'] - 2.0 * safe_x
    new_height = cell['height'] - 2.0 * safe_y

    return {
        'min_x': cell['min_x'] + safe_x,
        'max_x': cell['max_x'] - safe_x,
        'min_y': cell['min_y'] + safe_y,
        'max_y': cell['max_y'] - safe_y,
        'width': new_width,
        'height': new_height,
        'aspect': new_width / new_height,
        'area': new_width * new_height,
    }


def ck_random_mirror(island, uv_layer, mirror_u, mirror_v):
    """Adacığı kendi merkezine göre U ve/veya V ekseninde aynalar (yerleştirme SONRASI)."""
    if not (mirror_u or mirror_v):
        return

    bounds = ck_island_bounds(island, uv_layer)
    center_x = (bounds['min_x'] + bounds['max_x']) / 2.0
    center_y = (bounds['min_y'] + bounds['max_y']) / 2.0

    for face in island:
        for loop in face.loops:
            uv = loop[uv_layer].uv
            if mirror_u:
                uv.x = 2.0 * center_x - uv.x
            if mirror_v:
                uv.y = 2.0 * center_y - uv.y


def ck_fit_island_to_trim(island, uv_layer, bounds, cell, flip):
    """Trim hücresine sığdırır: kısa eksen hücre boyutuna kilitlenir (hotspot gibi),
    uzun eksen (tile eden) serbest bırakılır -- adacık kendi gerçek uzunluğuyla,
    hücrenin merkezine hizalı şekilde, hücre sınırlarının dışına taşarak yerleşir.
    Tek bir uniform scale kullanılır, böylece distorsiyon olmaz ve texel yoğunluğu
    her iki eksende de aynı kalır (tile dokusunun kayıksız görünmesi için önemli)."""
    island_short = min(bounds['width'], bounds['height'])
    cell_short = min(cell['width'], cell['height'])
    scale = cell_short / max(island_short, 0.000001)

    cell_center_x = (cell['min_x'] + cell['max_x']) / 2.0
    cell_center_y = (cell['min_y'] + cell['max_y']) / 2.0

    half_w = bounds['width'] / 2.0
    half_h = bounds['height'] / 2.0

    for face in island:
        for loop in face.loops:
            uv = loop[uv_layer].uv
            local_x = (uv.x - bounds['min_x']) - half_w
            local_y = (uv.y - bounds['min_y']) - half_h
            if flip:
                local_x, local_y = local_y, local_x
            uv.x = cell_center_x + local_x * scale
            uv.y = cell_center_y + local_y * scale


def ck_fit_island_to_cell(island, uv_layer, bounds, cell, flip):
    """Adacığı kendi bounding box'ından, hedef hücrenin bounding box'ına sığdırır (stretch).

    DİKKAT: flip=True olduğunda u/v takas edilir. Bu bir transpozedir; yani 90 derece
    dönüş + AYNA demektir, saf dönüş değil. Hotspot çeşitliliği için sorun olmaz ama
    yönlü detay içeren dokularda (yazı, tek yönlü gradyan, vida dizisi) görüntü ters
    döner. Saf 90 derece dönüş isteniyorsa (u, v) -> (v, 1 - u) kullanılmalı.
    """
    for face in island:
        for loop in face.loops:
            uv = loop[uv_layer].uv
            u = (uv.x - bounds['min_x']) / bounds['width']
            v = (uv.y - bounds['min_y']) / bounds['height']
            if flip:
                u, v = v, u
            loop[uv_layer].uv.x = cell['min_x'] + u * cell['width']
            loop[uv_layer].uv.y = cell['min_y'] + v * cell['height']


# ============================================================
# ORTAK HOTSPOT HAZIRLIK / VALIDASYON
# ============================================================

def ck_hotspot_prepare(context, operator):
    """Hotspot işlemleri için ortak validasyon + hazırlık.
    Hata varsa None döner (rapor operator üzerinden zaten yapılmış olur).
    Başarılıysa bir dict döner: obj, bm, uv_layer, islands, hotspot_cells, trim_cells,
    offset, variation, random_flip."""
    atlas_obj = context.scene.ck_hotspot_atlas
    if atlas_obj is None or atlas_obj.type != 'MESH':
        operator.report({'ERROR'}, "Önce bir Hotspot Atlas objesi seçmelisin.")
        return None

    obj = context.active_object
    if not obj or obj.type != 'MESH':
        operator.report({'ERROR'}, "Bir mesh objesi seçilmeli.")
        return None

    if atlas_obj == obj:
        operator.report({'ERROR'}, "Atlas objesi, üzerinde çalıştığın obje ile aynı olamaz.")
        return None

    if atlas_obj.mode == 'EDIT':
        # ck_read_atlas_cells object-mode mesh datasını okur; atlas edit mode'daysa
        # kullanıcının henüz yazılmamış değişiklikleri görünmez.
        operator.report({'WARNING'}, "Atlas objesi edit mode'da — son düzenlemeleri okunmayabilir.")

    cells = ck_read_atlas_cells(atlas_obj)
    if not cells:
        operator.report({'ERROR'}, "Atlas objesinde UV haritası / yüzey bulunamadı.")
        return None

    bm = bmesh.from_edit_mesh(obj.data)
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        operator.report({'ERROR'}, "Aktif objede UV haritası yok.")
        return None

    selected_faces = [f for f in bm.faces if f.select]
    if not selected_faces:
        operator.report({'ERROR'}, "Yüzey seçili değil!")
        return None

    islands = ck_find_islands_by_topology(selected_faces)

    return {
        'obj': obj,
        'bm': bm,
        'uv_layer': uv_layer,
        'islands': islands,
        'hotspot_cells': [c for c in cells if c['type'] == 'HOTSPOT'],
        'trim_cells': [c for c in cells if c['type'] == 'TRIM'],
        'offset': context.scene.ck_hotspot_offset,
        'variation': context.scene.ck_hotspot_variation,
        'random_flip': context.scene.ck_hotspot_random_flip,
        # Seed'li kendi RNG'miz: aynı seed aynı sonucu verir. Global random ile
        # redo panelinde (F9) her parametre oynatıldığında UV'ler zıplıyordu.
        'rng': random.Random(context.scene.ck_hotspot_seed),
    }


def ck_apply_random_flip(ctx, island):
    if ctx['random_flip']:
        rng = ctx['rng']
        ck_random_mirror(
            island, ctx['uv_layer'],
            mirror_u=rng.random() < 0.5,
            mirror_v=rng.random() < 0.5,
        )


# ============================================================
# OPERATÖRLER
# ============================================================

class UV_OT_StraightenStrip(bpy.types.Operator):
    """Aktif yüzeyi kare yap ve tüm şeridi ona göre hizala"""
    bl_idname = "uv.straighten_strip"
    bl_label = "Straighten UV Strip"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return ck_edit_mesh_poll(context)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Bir mesh objesi seçilmeli.")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            self.report({'ERROR'}, "Aktif objede UV haritası yok.")
            return {'CANCELLED'}

        active_face = bm.faces.active

        if not active_face or not active_face.select:
            self.report({'ERROR'}, "Aktif bir yüzey seçili değil!")
            return {'CANCELLED'}

        if len(active_face.loops) != 4:
            self.report({'ERROR'}, "Aktif yüzey bir Quad olmalı.")
            return {'CANCELLED'}

        # UV'leri kareye çek
        target_coords = [(0, 0), (1, 0), (1, 1), (0, 1)]
        for i, loop in enumerate(active_face.loops):
            loop[uv_layer].uv = target_coords[i]

        bmesh.update_edit_mesh(obj.data)

        # Follow Active Quads çalıştır
        bpy.ops.uv.follow_active_quads(mode='LENGTH_AVERAGE')

        return {'FINISHED'}


class UV_OT_MatchIslandsBounds(bpy.types.Operator):
    """Seçili tüm UV adacıklarını en büyük olanın boyutlarına getirip üst üste bindirir"""
    bl_idname = "uv.match_islands_bounds"
    bl_label = "Match UV Bounds"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return ck_edit_mesh_poll(context)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Bir mesh objesi seçilmeli.")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            self.report({'ERROR'}, "Aktif objede UV haritası yok.")
            return {'CANCELLED'}

        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({'ERROR'}, "Yüzey seçili değil!")
            return {'CANCELLED'}

        islands = ck_find_islands_by_topology(selected_faces)

        if len(islands) < 2:
            self.report({'WARNING'}, "Üst üste bindirmek için en az 2 farklı UV adacığı seçmelisiniz.")
            return {'CANCELLED'}

        island_bounds = [(island, ck_island_bounds(island, uv_layer)) for island in islands]

        largest_area = -1
        target_bounds = None
        target_island_index = -1
        for i, (island, bounds) in enumerate(island_bounds):
            if bounds['area'] > largest_area:
                largest_area = bounds['area']
                target_bounds = bounds
                target_island_index = i

        for i, (island, bounds) in enumerate(island_bounds):
            if i == target_island_index:
                continue
            ck_fit_island_to_cell(island, uv_layer, bounds, target_bounds, flip=False)

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"{len(islands)} adet UV adacığı başarıyla hizalandı.")
        return {'FINISHED'}


class UV_OT_HotspotMap(bpy.types.Operator):
    """Seçili UV adacıklarını, Hotspot Atlas objesindeki en yakın boyuttaki hücrelere
    OTOMATİK olarak yerleştirir (her adacık için Hotspot mu Trim mi daha uygunsa ona göre)"""
    bl_idname = "uv.hotspot_map"
    bl_label = "Hotspot Map (Otomatik)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return ck_edit_mesh_poll(context)

    def execute(self, context):
        ctx = ck_hotspot_prepare(context, self)
        if ctx is None:
            return {'CANCELLED'}

        for island in ctx['islands']:
            bounds = ck_island_bounds(island, ctx['uv_layer'])
            cell, flip, cell_type = ck_best_matching_cell_combined(
                ctx['hotspot_cells'], ctx['trim_cells'], bounds, ctx['variation'], ctx['rng']
            )

            if cell is None:
                continue

            if cell_type == 'TRIM':
                ck_fit_island_to_trim(island, ctx['uv_layer'], bounds, cell, flip)
            else:
                target_cell = ck_apply_cell_offset(cell, ctx['offset'])
                ck_fit_island_to_cell(island, ctx['uv_layer'], bounds, target_cell, flip)

            ck_apply_random_flip(ctx, island)

        bmesh.update_edit_mesh(ctx['obj'].data)
        self.report(
            {'INFO'},
            f"{len(ctx['islands'])} adacık yerleştirildi. "
            f"Atlas'ta {len(ctx['hotspot_cells'])} hotspot, {len(ctx['trim_cells'])} trim hücre bulundu."
        )
        return {'FINISHED'}


class UV_OT_HotspotMapStandard(bpy.types.Operator):
    """Seçili adacıkları SADECE standart Hotspot hücrelerine (sınırlı, stretch ile
    sığdırılan) yerleştirir. Trim hücreleri dikkate alınmaz."""
    bl_idname = "uv.hotspot_map_standard"
    bl_label = "Standart Hotspot"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return ck_edit_mesh_poll(context)

    def execute(self, context):
        ctx = ck_hotspot_prepare(context, self)
        if ctx is None:
            return {'CANCELLED'}

        if not ctx['hotspot_cells']:
            self.report({'ERROR'}, "Atlas'ta HOTSPOT (mavi/boyasız) hücre bulunamadı.")
            return {'CANCELLED'}

        placed = 0
        for island in ctx['islands']:
            bounds = ck_island_bounds(island, ctx['uv_layer'])
            cell, flip = ck_best_matching_hotspot_cell(
                ctx['hotspot_cells'], bounds, ctx['variation'], ctx['rng']
            )
            if cell is None:
                continue

            target_cell = ck_apply_cell_offset(cell, ctx['offset'])
            ck_fit_island_to_cell(island, ctx['uv_layer'], bounds, target_cell, flip)
            ck_apply_random_flip(ctx, island)
            placed += 1

        bmesh.update_edit_mesh(ctx['obj'].data)
        self.report({'INFO'}, f"{placed} adacık standart Hotspot hücrelerine yerleştirildi.")
        return {'FINISHED'}


class UV_OT_HotspotMapUnlimited(bpy.types.Operator):
    """Seçili adacıkları SADECE Trim (sınırsız / tile eden) hücrelerine yerleştirir.
    Adacığın kısa kenarı hücreye kilitlenir, uzun kenar serbest kalır (tile). Hotspot
    hücreleri dikkate alınmaz."""
    bl_idname = "uv.hotspot_map_unlimited"
    bl_label = "Sınırsız (Trim)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return ck_edit_mesh_poll(context)

    def execute(self, context):
        ctx = ck_hotspot_prepare(context, self)
        if ctx is None:
            return {'CANCELLED'}

        if not ctx['trim_cells']:
            self.report({'ERROR'}, "Atlas'ta TRIM (kırmızı) hücre bulunamadı.")
            return {'CANCELLED'}

        placed = 0
        for island in ctx['islands']:
            bounds = ck_island_bounds(island, ctx['uv_layer'])
            cell, flip = ck_best_matching_trim_cell(
                ctx['trim_cells'], bounds, ctx['variation'], ctx['rng']
            )
            if cell is None:
                continue

            ck_fit_island_to_trim(island, ctx['uv_layer'], bounds, cell, flip)
            ck_apply_random_flip(ctx, island)
            placed += 1

        bmesh.update_edit_mesh(ctx['obj'].data)
        self.report({'INFO'}, f"{placed} adacık Trim (sınırsız) hücrelerine yerleştirildi.")
        return {'FINISHED'}


# ============================================================
# PANEL
# ============================================================

class UV_PT_StraightenPanel(bpy.types.Panel):
    """UV Editor Yan Panel (N-Panel) UI"""
    bl_label = "UV Tools"
    bl_idname = "UV_PT_StraightenPanel"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Custom Tools'

    def draw(self, context):
        layout = self.layout

        if context.mode != 'EDIT_MESH':
            layout.label(text="Araçlar için Edit Mode'a geç", icon='INFO')

        col = layout.column(align=True)
        col.operator("uv.straighten_strip", icon='UV_DATA')

        layout.separator()

        col = layout.column(align=True)
        col.operator("uv.match_islands_bounds", icon='UV_ISLANDSEL')

        layout.separator()

        box = layout.box()
        box.label(text="Hotspot Mapping", icon='MOD_UVPROJECT')
        box.prop(context.scene, "ck_hotspot_atlas", text="Atlas")
        box.label(text="Atlas'ta: Kırmızı=Trim (tile), Mavi/boyasız=Hotspot", icon='INFO')
        box.prop(context.scene, "ck_hotspot_offset", text="İç Boşluk (Offset)")
        box.prop(context.scene, "ck_hotspot_variation", text="Çeşitlilik")
        box.prop(context.scene, "ck_hotspot_random_flip", text="Rastgele Flip (U/V)")
        box.prop(context.scene, "ck_hotspot_seed", text="Seed")

        enabled = context.scene.ck_hotspot_atlas is not None

        row = box.row(align=True)
        row.enabled = enabled
        row.operator("uv.hotspot_map_standard", icon='SNAP_FACE', text="Standart Hotspot")
        row.operator("uv.hotspot_map_unlimited", icon='MOD_UVPROJECT', text="Sınırsız (Trim)")

        col = box.column(align=True)
        col.enabled = enabled
        col.operator("uv.hotspot_map", icon='UV_SYNC_SELECT', text="Otomatik (Her İkisi)")


# ============================================================
# REGISTER / UNREGISTER
# ============================================================

classes = (
    UV_OT_StraightenStrip,
    UV_OT_MatchIslandsBounds,
    UV_OT_HotspotMap,
    UV_OT_HotspotMapStandard,
    UV_OT_HotspotMapUnlimited,
    UV_PT_StraightenPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.ck_hotspot_atlas = PointerProperty(
        type=bpy.types.Object,
        name="Hotspot Atlas",
        description="Seam'lerle hücrelere bölünmüş, UV'si açılmış hotspot şablonu (plane)",
        poll=lambda self, obj: obj.type == 'MESH',
    )

    bpy.types.Scene.ck_hotspot_offset = FloatProperty(
        name="İç Boşluk (Offset)",
        description="Adacığı, eşleşen hücrenin kenarlarından bu kadar (UV birimi, 0-1) içeri çeker",
        default=0.0,
        min=0.0,
        soft_max=0.1,
        precision=4,
    )

    bpy.types.Scene.ck_hotspot_variation = FloatProperty(
        name="Çeşitlilik",
        description="0 = her zaman en iyi eşleşen tek hücre. Yükseltirsen, benzer "
                     "boyuttaki diğer hücreler de aday olur ve adacıklar aralarına "
                     "rastgele dağıtılır (tek hücrede toplanmaz)",
        default=0.0,
        min=0.0,
        max=1.0,
    )

    bpy.types.Scene.ck_hotspot_random_flip = BoolProperty(
        name="Rastgele Flip (U/V)",
        description="Her adacığı yerleştirdikten sonra rastgele U, V veya her ikisinde "
                     "birden aynalar (tekrarı kırmak için)",
        default=False,
    )

    bpy.types.Scene.ck_hotspot_seed = IntProperty(
        name="Seed",
        description="Çeşitlilik ve rastgele flip için rastgelelik tohumu. Aynı seed "
                     "aynı sonucu verir; farklı bir dağılım istersen değiştir",
        default=0,
        min=0,
    )


def unregister():
    del bpy.types.Scene.ck_hotspot_seed
    del bpy.types.Scene.ck_hotspot_random_flip
    del bpy.types.Scene.ck_hotspot_variation
    del bpy.types.Scene.ck_hotspot_offset
    del bpy.types.Scene.ck_hotspot_atlas

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()