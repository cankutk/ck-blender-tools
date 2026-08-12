# SPDX-FileCopyrightText: 2026 Cankut
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import os
import re
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import StringProperty, PointerProperty

bl_info = {
    "name": "CK Varlık Bazlı LOD Düzenleyici ve Exporter",
    "author": "Cankut",
    "version": (1, 4),
    "blender": (3, 2, 0),
    "location": "View3D > Sidebar (N) > Asset Araçları",
    "description": "LOD hiyerarşisini düzenler ve gizli olsalar bile FBX olarak dışa aktarır.",
    "doc_url": "https://github.com/cankutk/ck-blender-tools",
    "tracker_url": "https://github.com/cankutk/ck-blender-tools/issues",
    "category": "Object",
}


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

# Blender, aynı isimde ikinci bir datablock oluşturulduğunda sonuna ".001" ekler.
# İsim eşleştirmelerimizin bu ekten etkilenmemesi için hep taban isme bakıyoruz;
# aksi halde "Foo_ASSET.001" hiçbir kontrole takılmaz ve her senkronizasyonda
# yeni bir kopya üretilir, export ise o collection'ı sessizce atlar.
_DUP_SUFFIX_RE = re.compile(r"\.\d{3}$")

# Windows'ta dosya adında kullanılamayan karakterler.
_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def ck_base_name(name):
    """'Foo_ASSET.001' -> 'Foo_ASSET'"""
    return _DUP_SUFFIX_RE.sub("", name)


def ck_has_suffix(name, suffix):
    return ck_base_name(name).endswith(suffix)


def ck_safe_filename(name):
    """Collection ismini dosya sisteminde güvenli bir dosya adına çevirir."""
    cleaned = _INVALID_FILENAME_RE.sub("_", name).strip().rstrip(".")
    return cleaned or "untitled"


def ck_find_child(parent_col, suffix):
    """parent_col altında verilen son eke sahip alt collection'ı döndürür."""
    for child in parent_col.children:
        if ck_has_suffix(child.name, suffix):
            return child
    return None


def ck_find_layer_col(layer_collection, col_name):
    """View layer ağacında isme göre LayerCollection arar.

    NOT: Bir collection birden fazla yere link'lenmişse birden fazla
    LayerCollection'ı olur; burada ilk bulunan kullanılır.
    """
    if layer_collection.name == col_name:
        return layer_collection
    for child in layer_collection.children:
        res = ck_find_layer_col(child, col_name)
        if res:
            return res
    return None


def ck_safe_hide_get(obj):
    """Obje view layer'da değilse hide_get() hata verir; None döndürüp geçiyoruz."""
    try:
        return obj.hide_get()
    except RuntimeError:
        return None


def ck_safe_hide_set(obj, value):
    if value is None:
        return
    try:
        obj.hide_set(value)
    except RuntimeError:
        pass


def ck_queue_preview(collection):
    """Asset ön izlemesini timer ile üretir.

    Timer'a doğrudan collection'ın bound method'unu vermiyoruz: kullanıcı bu
    0.1 saniye içinde undo yapar veya collection'ı silerse timer serbest
    bırakılmış bir datablock'a erişir ve Blender çöker. Bunun yerine ismi
    saklayıp tetiklendiğinde yeniden arıyoruz.
    """
    col_name = collection.name

    def _generate():
        col = bpy.data.collections.get(col_name)
        if col is not None and col.asset_data is not None:
            col.asset_generate_preview()
        return None

    bpy.app.timers.register(_generate, first_interval=0.1)


# ============================================================
# AYARLAR
# ============================================================

class AssetLODProperties(PropertyGroup):
    target_collection: StringProperty(
        name="Ana Collection",
        description="İçindeki objelerin düzenleneceği/export edileceği ana collection adı",
        default="EXPORTS"
    )
    export_path: StringProperty(
        name="Export Klasörü",
        description="FBX dosyalarının kaydedileceği klasör",
        default="//",
        subtype='DIR_PATH'
    )


# ============================================================
# OPERATÖRLER
# ============================================================

class OBJECT_OT_organize_lods(Operator):
    bl_idname = "object.organize_lods"
    bl_label = "Sistemi Senkronize Et"
    bl_description = "Seçili ana collection altındaki hiyerarşiyi düzenler ve ön izlemeleri üretir"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        target_name = context.scene.asset_lod_props.target_collection
        master_col = bpy.data.collections.get(target_name)

        if not master_col:
            self.report({'WARNING'}, f"'{target_name}' adında bir collection bulunamadı!")
            return {'CANCELLED'}

        created = 0
        updated = 0
        moved = 0
        skipped = []

        for item_col in master_col.children:
            asset_col = ck_find_child(item_col, "_ASSET")
            lod_col = ck_find_child(item_col, "_LOD")
            is_new = asset_col is None and lod_col is None

            # Eksik olanı tamamla. Sadece biri varsa da doğru davranır; eskiden
            # "ikisi birden var mı" kontrolü yüzünden yarım kalmış hiyerarşiler
            # her çalıştırmada yeni kopya üretiyordu.
            if asset_col is None:
                asset_col = bpy.data.collections.new(f"{item_col.name}_ASSET")
                asset_col.color_tag = 'COLOR_04'
                item_col.children.link(asset_col)

            if lod_col is None:
                lod_col = bpy.data.collections.new(f"{item_col.name}_LOD")
                lod_col.color_tag = 'COLOR_06'
                item_col.children.link(lod_col)

            for obj in list(item_col.objects):
                if obj.type == 'MESH':
                    obj.data.name = obj.name

                if "_LOD0" in obj.name:
                    target_col = asset_col
                elif "_LOD" in obj.name:
                    target_col = lod_col
                else:
                    # İsimlendirme kuralına uymayan objeler item_col'da kalır ve
                    # export'a dahil edilmez -- kullanıcıya bunu söylemek şart.
                    skipped.append(obj.name)
                    continue

                target_col.objects.link(obj)
                item_col.objects.unlink(obj)
                moved += 1

            if not asset_col.asset_data:
                asset_col.asset_mark()
            ck_queue_preview(asset_col)

            if is_new:
                created += 1
            else:
                updated += 1

        msg = f"Tamamlandı — yeni: {created}, güncellenen: {updated}, taşınan obje: {moved}"

        if skipped:
            preview = ", ".join(skipped[:5])
            if len(skipped) > 5:
                preview += f" (+{len(skipped) - 5} tane daha)"
            self.report(
                {'WARNING'},
                f"{msg}. UYARI: {len(skipped)} obje isminde '_LOD' geçmediği için "
                f"taşınmadı ve export edilmeyecek: {preview}"
            )
        else:
            self.report({'INFO'}, msg)

        return {'FINISHED'}


class OBJECT_OT_export_lods(Operator):
    bl_idname = "object.export_lods"
    bl_label = "FBX Olarak Toplu Dışa Aktar"
    bl_description = "Düzenlenmiş assetleri belirtilen klasöre FBX olarak kaydeder (Gizli olanları geçici olarak açar)"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        props = context.scene.asset_lod_props
        master_col = bpy.data.collections.get(props.target_collection)

        if not master_col:
            self.report({'WARNING'}, f"'{props.target_collection}' adında bir collection bulunamadı!")
            return {'CANCELLED'}

        if not props.export_path:
            self.report({'WARNING'}, "Lütfen bir dışa aktarma klasörü seçin!")
            return {'CANCELLED'}

        if props.export_path.startswith("//") and not bpy.data.filepath:
            self.report({'ERROR'}, "Göreli yol (//) kullanmak için önce .blend dosyasını kaydet.")
            return {'CANCELLED'}

        export_dir = bpy.path.abspath(props.export_path)
        try:
            os.makedirs(export_dir, exist_ok=True)
        except OSError as err:
            self.report({'ERROR'}, f"Export klasörü oluşturulamadı: {err}")
            return {'CANCELLED'}

        view_layer = context.view_layer
        exported = 0
        failed = []

        bpy.ops.object.select_all(action='DESELECT')

        for item_col in master_col.children:
            objects_to_export = []
            cols_to_check = [master_col, item_col]

            for sub_col in item_col.children:
                if ck_has_suffix(sub_col.name, "_ASSET") or ck_has_suffix(sub_col.name, "_LOD"):
                    objects_to_export.extend(sub_col.objects)
                    cols_to_check.append(sub_col)

            if not objects_to_export:
                continue

            # Obje gizlilik durumlarını collection'lara DOKUNMADAN ÖNCE kaydet:
            # bir collection'ın exclude'unu değiştirmek içindeki objelerin local
            # hide durumunu sıfırlar, sonra kaydedersek orijinali kaybederiz.
            obj_states = {
                obj.name: (ck_safe_hide_get(obj), obj.hide_viewport)
                for obj in objects_to_export
            }
            col_states = {}

            try:
                for col in cols_to_check:
                    lc = ck_find_layer_col(view_layer.layer_collection, col.name)
                    if lc:
                        col_states[col.name] = (lc.exclude, lc.hide_viewport)
                        lc.exclude = False
                        lc.hide_viewport = False

                selected = []
                for obj in objects_to_export:
                    obj.hide_viewport = False
                    ck_safe_hide_set(obj, False)
                    try:
                        obj.select_set(True)
                        selected.append(obj)
                    except RuntimeError:
                        # Obje bu view layer'da yok (ör. başka bir scene'de).
                        pass

                if not selected:
                    failed.append(f"{item_col.name}: objeler bu view layer'da değil")
                    continue

                view_layer.objects.active = selected[0]
                filepath = os.path.join(export_dir, f"{ck_safe_filename(item_col.name)}.fbx")

                bpy.ops.export_scene.fbx(
                    filepath=filepath,
                    use_selection=True,
                    object_types={'EMPTY', 'CAMERA', 'LIGHT', 'ARMATURE', 'MESH', 'OTHER'},
                    path_mode='AUTO',
                    batch_mode='OFF',
                    global_scale=1.0,
                    apply_scale_options='FBX_SCALE_ALL',
                    axis_forward='-Z',
                    axis_up='Y',
                    apply_unit_scale=False,
                    use_space_transform=True,
                    bake_space_transform=True
                )
                exported += 1

            except Exception as err:
                failed.append(f"{item_col.name}: {err}")

            finally:
                # Export patlasa bile sahneyi kullanıcının bıraktığı hâle döndür.
                for obj in objects_to_export:
                    state = obj_states.get(obj.name)
                    if state is None:
                        continue
                    ck_safe_hide_set(obj, state[0])
                    obj.hide_viewport = state[1]

                # LayerCollection pointer'ları exclude değişince geçersizleşebildiği
                # için state'i isme göre saklayıp lc'yi burada yeniden arıyoruz.
                for col in reversed(cols_to_check):
                    state = col_states.get(col.name)
                    if state is None:
                        continue
                    lc = ck_find_layer_col(view_layer.layer_collection, col.name)
                    if lc:
                        lc.exclude, lc.hide_viewport = state

                bpy.ops.object.select_all(action='DESELECT')

        if failed:
            detail = "; ".join(failed[:3])
            if len(failed) > 3:
                detail += f" (+{len(failed) - 3} tane daha)"
            self.report({'WARNING'}, f"{exported} FBX aktarıldı, {len(failed)} başarısız — {detail}")
        else:
            self.report({'INFO'}, f"{exported} adet FBX başarıyla dışa aktarıldı!")

        return {'FINISHED'}


# ============================================================
# PANEL
# ============================================================

class VIEW3D_PT_organize_lods(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Asset Araçları'
    bl_label = "Varlık Bazlı LOD ve Export"

    def draw(self, context):
        layout = self.layout
        props = context.scene.asset_lod_props

        layout.prop(props, "target_collection")
        layout.operator("object.organize_lods", text="Sistemi Senkronize Et", icon='FILE_REFRESH')

        layout.separator()

        layout.prop(props, "export_path")

        if context.mode != 'OBJECT':
            layout.label(text="Export için Object Mode'a geç", icon='INFO')

        layout.operator("object.export_lods", text="FBX Olarak Dışa Aktar", icon='EXPORT')


classes = (
    AssetLODProperties,
    OBJECT_OT_organize_lods,
    OBJECT_OT_export_lods,
    VIEW3D_PT_organize_lods
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.asset_lod_props = PointerProperty(type=AssetLODProperties)


def unregister():
    del bpy.types.Scene.asset_lod_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
