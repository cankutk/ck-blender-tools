# SPDX-FileCopyrightText: 2026 Cankut
#
# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {
    "name": "CK Vertex Painter",
    "author": "Cankut",
    "version": (4, 1),
    # color_attributes ve bm.loops.layers.float_color API'leri 3.2 ile geldi.
    "blender": (3, 2, 0),
    "location": "View3D > Sidebar > Vertex Paint",
    "description": "10 Gradyan Palet + Özel Palet ile vertex boyama",
    "doc_url": "https://github.com/cankutk/ck-blender-tools",
    "tracker_url": "https://github.com/cankutk/ck-blender-tools/issues",
    "category": "Mesh",
}

import bpy
import bmesh
from bpy.app.handlers import persistent

ATTR_NAME = "Color"


# --- YARDIMCI ---
def ck_find_loop_color_layer(bm, name):
    """Face-corner renk katmanını isimle bulur.

    bm.loops.layers.color sadece BYTE_COLOR katmanlarını görür; mesh'te aynı
    isimde bir FLOAT_COLOR attribute varsa orada bulunamaz. İki koleksiyona da
    bakıyoruz.
    """
    if name in bm.loops.layers.color:
        return bm.loops.layers.color[name]
    if name in bm.loops.layers.float_color:
        return bm.loops.layers.float_color[name]
    return None


# --- VERTEX BOYAMA OPERATÖRÜ ---
class MESH_OT_assign_vertex_color(bpy.types.Operator):
    """Seçili yüzlere vertex rengi uygular"""
    bl_idname = "mesh.assign_vertex_color"
    bl_label = "Renk Uygula"
    bl_options = {'REGISTER', 'UNDO'}

    color: bpy.props.FloatVectorProperty(
        name="Renk",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0)
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        obj = context.edit_object
        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "Lütfen mesh objesini edit mode'da seçin")
            return {'CANCELLED'}

        me = obj.data

        # Mevcut "Color" attribute'u vertex (POINT) domain'indeyse bizim
        # face-corner boyamamız için kullanılamaz; yeni bir tane açıyoruz.
        # Blender isim çakışmasında ".001" ekleyebildiği için dönen
        # attribute'un gerçek adını saklıyoruz.
        attr = me.color_attributes.get(ATTR_NAME)
        created = attr is None or attr.domain != 'CORNER'
        if created:
            attr = me.color_attributes.new(name=ATTR_NAME, type='BYTE_COLOR', domain='CORNER')

        layer_name = attr.name
        me.color_attributes.active = attr

        # Operatör F3 arama menüsünden veya 3D View dışından çağrılabilir;
        # o durumda space_data'da shading olmayabilir.
        shading = getattr(context.space_data, "shading", None)
        if shading is not None and shading.type == 'SOLID':
            shading.color_type = 'VERTEX'

        bm = bmesh.from_edit_mesh(me)
        color_layer = ck_find_loop_color_layer(bm, layer_name)

        if color_layer is None:
            self.report({'ERROR'}, f"'{layer_name}' renk katmanı bulunamadı")
            return {'CANCELLED'}

        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({'WARNING'}, "Lütfen boyamak için yüz seçin")
            return {'CANCELLED'}

        # Katmanı YENİ oluşturduysak tabanı beyaza çekiyoruz (yeni katman siyah
        # başlar). Eskiden bunun yerine "mesh tamamen siyah mı" diye tüm loop'lar
        # taranıyordu: hem her tıklamada yavaştı, hem de kasten siyaha boyanmış
        # bir mesh'in tüm boyasını siliyordu.
        if created:
            for f in bm.faces:
                for l in f.loops:
                    l[color_layer] = (1.0, 1.0, 1.0, 1.0)

        for face in selected_faces:
            for loop in face.loops:
                loop[color_layer] = self.color

        bmesh.update_edit_mesh(me)
        self.report({'INFO'}, f"{len(selected_faces)} yüz boyandı")
        return {'FINISHED'}


# --- ÖZEL PALETE RENK EKLEME ---
class MESH_OT_add_custom_color(bpy.types.Operator):
    """Özel palete yeni renk ekler"""
    bl_idname = "mesh.add_custom_color"
    bl_label = "Renk Ekle"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.vertex_painter_settings

        if len(settings.custom_palette) >= 25:
            self.report({'WARNING'}, "Özel palet dolu (maksimum 25 renk)")
            return {'CANCELLED'}

        item = settings.custom_palette.add()
        item.color = settings.active_color

        self.report({'INFO'}, f"Renk eklendi ({len(settings.custom_palette)}/25)")
        return {'FINISHED'}


# --- ÖZEL PALETTEN RENK SİLME ---
class MESH_OT_remove_custom_color(bpy.types.Operator):
    """Özel paletten renk siler"""
    bl_idname = "mesh.remove_custom_color"
    bl_label = "Renk Sil"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        settings = context.scene.vertex_painter_settings

        if 0 <= self.index < len(settings.custom_palette):
            settings.custom_palette.remove(self.index)
            self.report({'INFO'}, "Renk silindi")
            return {'FINISHED'}

        return {'CANCELLED'}


# --- ÖZEL PALETİ TEMİZLE ---
class MESH_OT_clear_custom_palette(bpy.types.Operator):
    """Özel paletteki tüm renkleri siler"""
    bl_idname = "mesh.clear_custom_palette"
    bl_label = "Paleti Temizle"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.vertex_painter_settings
        settings.custom_palette.clear()
        self.report({'INFO'}, "Özel palet temizlendi")
        return {'FINISHED'}


# --- SABİT PALETLERİ VARSAYILANA DÖNDÜR ---
class MESH_OT_reset_palettes(bpy.types.Operator):
    """Sabit paletlerdeki renkleri varsayılan hâline döndürür"""
    bl_idname = "mesh.reset_vertex_palettes"
    bl_label = "Paletleri Sıfırla"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.vertex_painter_settings
        for _, colors, palette_attr in ALL_PALETTES:
            palette = getattr(settings, palette_attr)
            palette.clear()
            for color in colors:
                palette.add().color = color
        self.report({'INFO'}, "Sabit paletler varsayılana döndürüldü")
        return {'FINISHED'}


# --- RENK SLOT PROPERTY ---
class ColorSlot(bpy.types.PropertyGroup):
    color: bpy.props.FloatVectorProperty(
        name="Renk",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.5, 0.5, 0.5, 1.0)
    )


# --- ANA AYARLAR ---
class VertexPainterSettings(bpy.types.PropertyGroup):
    active_color: bpy.props.FloatVectorProperty(
        name="Aktif Renk",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 0.0, 0.0, 1.0)
    )

    # Palet görünürlük ayarları
    show_palette_1: bpy.props.BoolProperty(name="Palet 1", default=True)
    show_palette_2: bpy.props.BoolProperty(name="Palet 2", default=True)
    show_palette_3: bpy.props.BoolProperty(name="Palet 3", default=True)
    show_palette_4: bpy.props.BoolProperty(name="Palet 4", default=True)
    show_palette_5: bpy.props.BoolProperty(name="Palet 5", default=True)
    show_palette_6: bpy.props.BoolProperty(name="Palet 6", default=True)
    show_palette_7: bpy.props.BoolProperty(name="Palet 7", default=True)
    show_palette_8: bpy.props.BoolProperty(name="Palet 8", default=True)
    show_palette_9: bpy.props.BoolProperty(name="Palet 9", default=True)
    show_palette_10: bpy.props.BoolProperty(name="Palet 10", default=True)
    show_custom_palette: bpy.props.BoolProperty(name="Özel Palet", default=True)

    # Sabit paletler
    palette_1: bpy.props.CollectionProperty(type=ColorSlot)
    palette_2: bpy.props.CollectionProperty(type=ColorSlot)
    palette_3: bpy.props.CollectionProperty(type=ColorSlot)
    palette_4: bpy.props.CollectionProperty(type=ColorSlot)
    palette_5: bpy.props.CollectionProperty(type=ColorSlot)
    palette_6: bpy.props.CollectionProperty(type=ColorSlot)
    palette_7: bpy.props.CollectionProperty(type=ColorSlot)
    palette_8: bpy.props.CollectionProperty(type=ColorSlot)
    palette_9: bpy.props.CollectionProperty(type=ColorSlot)
    palette_10: bpy.props.CollectionProperty(type=ColorSlot)
    custom_palette: bpy.props.CollectionProperty(type=ColorSlot)


# --- SABİT RENKLER - 10 GRADYAN PALET ---
PALETTE_1 = [(1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0), (1.0, 1.0, 0.0, 1.0), (0.0, 1.0, 1.0, 1.0)]
PALETTE_2 = [(0.9, 0.95, 1.0, 1.0), (0.75, 0.85, 1.0, 1.0), (0.6, 0.75, 1.0, 1.0), (0.4, 0.65, 0.95, 1.0), (0.2, 0.5, 0.9, 1.0), (0.0, 0.4, 0.85, 1.0), (0.0, 0.3, 0.7, 1.0), (0.0, 0.2, 0.55, 1.0), (0.0, 0.1, 0.4, 1.0), (0.0, 0.05, 0.25, 1.0)]
PALETTE_3 = [(1.0, 0.9, 0.9, 1.0), (1.0, 0.95, 0.85, 1.0), (1.0, 1.0, 0.85, 1.0), (0.95, 1.0, 0.9, 1.0), (0.85, 1.0, 0.95, 1.0), (0.85, 0.95, 1.0, 1.0), (0.9, 0.9, 1.0, 1.0), (0.95, 0.85, 1.0, 1.0), (1.0, 0.9, 0.95, 1.0), (0.95, 0.9, 0.85, 1.0)]
PALETTE_4 = [(1.0, 0.95, 0.95, 1.0), (1.0, 0.85, 0.85, 1.0), (1.0, 0.7, 0.7, 1.0), (1.0, 0.5, 0.5, 1.0), (1.0, 0.3, 0.3, 1.0), (0.95, 0.15, 0.15, 1.0), (0.8, 0.0, 0.0, 1.0), (0.65, 0.0, 0.0, 1.0), (0.5, 0.0, 0.1, 1.0), (0.35, 0.0, 0.05, 1.0)]
PALETTE_5 = [(0.95, 1.0, 0.95, 1.0), (0.85, 1.0, 0.85, 1.0), (0.7, 0.95, 0.7, 1.0), (0.5, 0.9, 0.5, 1.0), (0.3, 0.8, 0.3, 1.0), (0.2, 0.7, 0.2, 1.0), (0.1, 0.6, 0.1, 1.0), (0.05, 0.45, 0.05, 1.0), (0.1, 0.35, 0.1, 1.0), (0.05, 0.25, 0.05, 1.0)]
PALETTE_6 = [(1.0, 1.0, 0.95, 1.0), (1.0, 0.95, 0.7, 1.0), (1.0, 0.9, 0.4, 1.0), (1.0, 0.8, 0.2, 1.0), (1.0, 0.65, 0.0, 1.0), (1.0, 0.5, 0.0, 1.0), (0.95, 0.35, 0.0, 1.0), (0.9, 0.2, 0.1, 1.0), (0.7, 0.1, 0.15, 1.0), (0.5, 0.05, 0.15, 1.0)]
PALETTE_7 = [(0.9, 1.0, 1.0, 1.0), (0.75, 0.95, 0.95, 1.0), (0.5, 0.9, 0.9, 1.0), (0.3, 0.85, 0.85, 1.0), (0.1, 0.75, 0.75, 1.0), (0.0, 0.65, 0.7, 1.0), (0.0, 0.55, 0.65, 1.0), (0.0, 0.45, 0.6, 1.0), (0.0, 0.35, 0.5, 1.0), (0.0, 0.25, 0.4, 1.0)]
PALETTE_8 = [(0.95, 0.9, 1.0, 1.0), (0.85, 0.75, 1.0, 1.0), (0.75, 0.6, 0.95, 1.0), (0.65, 0.45, 0.9, 1.0), (0.55, 0.3, 0.85, 1.0), (0.5, 0.2, 0.75, 1.0), (0.4, 0.1, 0.65, 1.0), (0.3, 0.05, 0.5, 1.0), (0.25, 0.0, 0.4, 1.0), (0.15, 0.0, 0.25, 1.0)]
PALETTE_9 = [(0.95, 0.9, 0.85, 1.0), (0.9, 0.8, 0.65, 1.0), (0.85, 0.7, 0.5, 1.0), (0.75, 0.6, 0.4, 1.0), (0.65, 0.5, 0.3, 1.0), (0.55, 0.4, 0.25, 1.0), (0.45, 0.3, 0.15, 1.0), (0.35, 0.22, 0.1, 1.0), (0.25, 0.15, 0.08, 1.0), (0.15, 0.1, 0.05, 1.0)]
PALETTE_10 = [(1.0, 1.0, 1.0, 1.0), (0.9, 0.9, 0.9, 1.0), (0.8, 0.8, 0.8, 1.0), (0.7, 0.7, 0.7, 1.0), (0.6, 0.6, 0.6, 1.0), (0.5, 0.5, 0.5, 1.0), (0.4, 0.4, 0.4, 1.0), (0.3, 0.3, 0.3, 1.0), (0.15, 0.15, 0.15, 1.0), (0.0, 0.0, 0.0, 1.0)]

ALL_PALETTES = [
    ("Ana Renkler (RGB+)", PALETTE_1, "palette_1"),
    ("Mavi Gradyan", PALETTE_2, "palette_2"),
    ("Pastel Renkler", PALETTE_3, "palette_3"),
    ("Kırmızı Gradyan", PALETTE_4, "palette_4"),
    ("Yeşil Gradyan", PALETTE_5, "palette_5"),
    ("Gün Batımı", PALETTE_6, "palette_6"),
    ("Okyanus", PALETTE_7, "palette_7"),
    ("Mor Gradyan", PALETTE_8, "palette_8"),
    ("Toprak Tonları", PALETTE_9, "palette_9"),
    ("Gri Skala", PALETTE_10, "palette_10"),
]


# --- ARAYÜZ PANEL ---
class VIEW3D_PT_vertex_painter(bpy.types.Panel):
    """Vertex Paint paneli"""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Vertex Paint'
    bl_label = "CK Vertex Painter"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.vertex_painter_settings

        info_box = layout.box()
        info_box.label(text="Vertex Renk Boyama", icon='BRUSHES_ALL')
        info_box.label(text="Edit Mode'da yüz seçip renk tıklayın", icon='INFO')

        layout.separator()

        box = layout.box()
        box.label(text="Aktif Renk", icon='COLOR')
        box.prop(settings, "active_color", text="")

        layout.separator()

        for idx, (name, colors, palette_attr) in enumerate(ALL_PALETTES, 1):
            self.draw_collapsible_palette(
                layout, context, colors, f"Palet {idx}: {name}",
                f"show_palette_{idx}", getattr(settings, palette_attr)
            )
            layout.separator()

        layout.operator("mesh.reset_vertex_palettes", text="Sabit Paletleri Sıfırla", icon='LOOP_BACK')

        layout.separator()

        self.draw_custom_palette(layout, context)

    def draw_collapsible_palette(self, layout, context, colors, title, prop_name, palette_obj):
        settings = context.scene.vertex_painter_settings
        box = layout.box()

        row = box.row()
        row.prop(settings, prop_name, text=title,
                 icon='DOWNARROW_HLT' if getattr(settings, prop_name) else 'RIGHTARROW',
                 emboss=False)

        if not getattr(settings, prop_name):
            return

        color_count = len(colors)
        columns = 5
        rows_needed = (color_count + columns - 1) // columns
        color_index = 0

        for row_idx in range(rows_needed):
            row = box.row(align=True)
            colors_in_row = min(columns, color_count - color_index)

            for i in range(colors_in_row):
                if color_index >= color_count:
                    break

                col = row.column(align=True)

                # Uygulanacak renk, kullanıcının düzenleyebildiği slot'tan gelir.
                # (Eskiden picker düzenlenebilir görünüyordu ama buton hep
                # hardcoded sabiti gönderiyordu, yani düzenleme hiç işe yaramıyordu.)
                if color_index < len(palette_obj):
                    slot = palette_obj[color_index]
                    preview = col.row(align=True)
                    preview.scale_y = 1.5
                    preview.prop(slot, "color", text="")
                    apply_color = slot.color[:]
                else:
                    apply_color = colors[color_index]

                apply_op = col.operator("mesh.assign_vertex_color", text="", icon='FILE_TICK', emboss=True)
                apply_op.color = apply_color

                color_index += 1

    def draw_custom_palette(self, layout, context):
        settings = context.scene.vertex_painter_settings
        box = layout.box()

        row = box.row()
        row.prop(settings, "show_custom_palette", text="Özel Palet (Düzenlenebilir)",
                 icon='DOWNARROW_HLT' if settings.show_custom_palette else 'RIGHTARROW',
                 emboss=False)

        if not settings.show_custom_palette:
            return

        control = box.row(align=True)
        control.operator("mesh.add_custom_color", text="Ekle", icon='ADD')
        control.operator("mesh.clear_custom_palette", text="Temizle", icon='TRASH')

        info = box.row()
        info.label(text=f"Toplam: {len(settings.custom_palette)}/25", icon='INFO')

        box.separator()

        if len(settings.custom_palette) == 0:
            box.label(text="Henüz renk eklenmedi", icon='ERROR')
            return

        custom_colors = settings.custom_palette
        current_row = None

        for idx, color_slot in enumerate(custom_colors):
            if idx % 5 == 0:
                current_row = box.row(align=True)

            col = current_row.column(align=True)

            picker = col.row(align=True)
            picker.scale_y = 1.5
            picker.prop(color_slot, "color", text="")

            btn_row = col.row(align=True)
            apply = btn_row.operator("mesh.assign_vertex_color", text="", icon='FILE_TICK')
            apply.color = color_slot.color[:]
            remove = btn_row.operator("mesh.remove_custom_color", text="", icon='X')
            remove.index = idx


# --- BAŞLATMA ---
def initialize_palettes(scene):
    """Paletleri renklerle doldur"""
    settings = getattr(scene, "vertex_painter_settings", None)
    if settings is None:
        return
    for _, colors, palette_attr in ALL_PALETTES:
        palette = getattr(settings, palette_attr)
        if len(palette) == 0:  # Sadece boşsa doldur
            for color in colors:
                item = palette.add()
                item.color = color


def ck_initialize_all_scenes():
    """Timer callback: mevcut tüm sahnelerin paletlerini doldurur."""
    for scene in bpy.data.scenes:
        initialize_palettes(scene)
    return None


@persistent
def load_handler(dummy):
    """Blender açıldığında veya yeni dosya yüklendiğinde paletleri doldur"""
    for scene in bpy.data.scenes:
        initialize_palettes(scene)


# --- KAYIT ---
classes = (
    ColorSlot,
    VertexPainterSettings,
    MESH_OT_assign_vertex_color,
    MESH_OT_add_custom_color,
    MESH_OT_remove_custom_color,
    MESH_OT_clear_custom_palette,
    MESH_OT_reset_palettes,
    VIEW3D_PT_vertex_painter,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.vertex_painter_settings = bpy.props.PointerProperty(type=VertexPainterSettings)

    if load_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(load_handler)

    # load_post sadece dosya AÇILDIĞINDA tetiklenir. Eklenti yeni etkinleştirildiğinde
    # de paletleri doldurmalıyız, aksi halde kullanıcı bir dosya açana kadar sabit
    # paletlerde hiç renk seçici görmez.
    bpy.app.timers.register(ck_initialize_all_scenes, first_interval=0.1)


def unregister():
    if load_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(load_handler)

    if bpy.app.timers.is_registered(ck_initialize_all_scenes):
        bpy.app.timers.unregister(ck_initialize_all_scenes)

    del bpy.types.Scene.vertex_painter_settings

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
