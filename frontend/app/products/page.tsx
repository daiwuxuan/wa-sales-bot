"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Product, type Variant } from "@/lib/api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  PageHeader,
  Spinner,
  inputClass,
} from "@/components/ui";

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openSku, setOpenSku] = useState<string | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const fileInputs = useRef<Record<string, HTMLInputElement | null>>({});

  const load = useCallback(async () => {
    try {
      const data = await api.get<{ items: Product[] }>("/api/products");
      setProducts(data.items);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
  // 下面这行豁免的是误报：load() 里的 setState 全部在 await 之后，
  // effect 体内没有任何同步 setState。这条规则只看调用点，不跨 async
  // 边界追踪。React 官方文档认可「在 await 之后 setState」这种取数写法。
  // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  async function uploadImage(sku: string, file: File) {
    setUploading(sku); // 由交互设置，不在 effect 里

    setError(null);
    try {
      await api.upload(`/api/products/${sku}/image`, file);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(null);
    }
  }

  async function removeImage(sku: string) {
    if (!confirm("删除这张商品图？AI 之后将无法给客户发图。")) return;
    try {
      // 这个接口会同时清掉 DB 字段和磁盘文件
      await api.del(`/api/products/${sku}/image`);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div>
      <PageHeader
        title="商品管理"
        description="商品图由你自己上传 —— AI 不生成、不合成图片，只负责读取这里配好的图片地址发给客户。价格和最低价也在这一页维护。"
      />

      <ErrorNote error={error} />

      {loading ? (
        <Card>
          <Spinner />
        </Card>
      ) : products.length === 0 ? (
        <Card>
          <EmptyState title="还没有商品" hint="运行 python seed.py 灌入演示数据" />
        </Card>
      ) : (
        <div className="space-y-3">
          {products.map((product) => (
            <Card key={product.sku}>
              <div className="flex flex-col gap-4 p-4 sm:flex-row">
                {/* 商品图 */}
                <div className="shrink-0">
                  <div className="relative h-28 w-28 overflow-hidden rounded-lg border border-border bg-surface-2">
                    {product.image_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={product.image_url}
                        alt={product.name}
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center px-2 text-center text-[11px] text-faint">
                        还没有图片
                      </div>
                    )}
                  </div>
                  <input
                    ref={(el) => {
                      fileInputs.current[product.sku] = el;
                    }}
                    type="file"
                    accept="image/jpeg,image/png,image/webp,image/gif"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) uploadImage(product.sku, file);
                      e.target.value = "";
                    }}
                  />
                  <div className="mt-2 flex gap-1.5">
                    <Button
                      variant="secondary"
                      className="!px-2 !py-1 !text-[11px]"
                      disabled={uploading === product.sku}
                      onClick={() => fileInputs.current[product.sku]?.click()}
                    >
                      {uploading === product.sku
                        ? "上传中…"
                        : product.image_url
                          ? "换图"
                          : "上传"}
                    </Button>
                    {product.image_url && (
                      <Button
                        variant="ghost"
                        className="!px-2 !py-1 !text-[11px]"
                        onClick={() => removeImage(product.sku)}
                      >
                        删除
                      </Button>
                    )}
                  </div>
                </div>

                {/* 商品信息 */}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-muted">{product.sku}</span>
                    <h3 className="text-sm font-semibold text-text">{product.name}</h3>
                    {product.category && <Badge>{product.category}</Badge>}
                    {product.total_stock === 0 && <Badge tone="danger">全部缺货</Badge>}
                  </div>
                  <p className="mt-1 text-xs text-faint">
                    {product.variant_count} 个变体 · 可售 {product.total_stock} 件
                  </p>
                  <button
                    className="mt-2 text-xs text-accent hover:underline"
                    onClick={() => setOpenSku(openSku === product.sku ? null : product.sku)}
                  >
                    {openSku === product.sku ? "收起变体" : "展开变体改价改库存"}
                  </button>
                </div>
              </div>

              {openSku === product.sku && (
                <div className="border-t border-border px-4 py-3">
                  <VariantTable variants={product.variants} onSaved={load} onError={setError} />
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function VariantTable({
  variants,
  onSaved,
  onError,
}: {
  variants: Variant[];
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-faint">
            <th className="py-1.5 pr-3 font-normal">颜色</th>
            <th className="py-1.5 pr-3 font-normal">尺码</th>
            <th className="py-1.5 pr-3 font-normal">售价</th>
            <th className="py-1.5 pr-3 font-normal">最低价</th>
            <th className="py-1.5 pr-3 font-normal">库存</th>
            <th className="py-1.5 font-normal" />
          </tr>
        </thead>
        <tbody>
          {variants.map((variant) => (
            <VariantRow
              key={variant.id}
              variant={variant}
              onSaved={onSaved}
              onError={onError}
            />
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] leading-relaxed text-faint">
        最低价是硬底线：折扣叠加后也不会低于它，低于它的订单在数据库层面就写不进去。
        这个数字永远不会下发给 AI。
      </p>
    </div>
  );
}

function VariantRow({
  variant,
  onSaved,
  onError,
}: {
  variant: Variant;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const [price, setPrice] = useState((variant.list_price_cents / 100).toFixed(2));
  const [floor, setFloor] = useState((variant.floor_price_cents / 100).toFixed(2));
  const [stock, setStock] = useState(String(variant.qty_on_hand));
  const [saving, setSaving] = useState(false);

  const dirty =
    price !== (variant.list_price_cents / 100).toFixed(2) ||
    floor !== (variant.floor_price_cents / 100).toFixed(2) ||
    stock !== String(variant.qty_on_hand);

  async function save() {
    setSaving(true);
    try {
      await api.patch(`/api/products/variants/${variant.id}`, {
        list_price: Number(price),
        floor_price: Number(floor),
        qty_on_hand: Number(stock),
      });
      onSaved();
    } catch (e) {
      onError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <tr className="border-t border-border">
      <td className="py-1.5 pr-3">{variant.color_label}</td>
      <td className="py-1.5 pr-3">{variant.size_label}</td>
      <td className="py-1.5 pr-3">
        <input
          className={`${inputClass} !w-24 !py-1 !text-xs`}
          value={price}
          onChange={(e) => setPrice(e.target.value)}
        />
      </td>
      <td className="py-1.5 pr-3">
        <input
          className={`${inputClass} !w-24 !py-1 !text-xs`}
          value={floor}
          onChange={(e) => setFloor(e.target.value)}
        />
      </td>
      <td className="py-1.5 pr-3">
        <input
          className={`${inputClass} !w-20 !py-1 !text-xs`}
          value={stock}
          onChange={(e) => setStock(e.target.value)}
        />
        {variant.qty_reserved > 0 && (
          <span className="ml-1.5 text-[10px] text-faint">
            预占 {variant.qty_reserved}
          </span>
        )}
      </td>
      <td className="py-1.5">
        <Button
          variant={dirty ? "primary" : "ghost"}
          className="!px-2 !py-1 !text-[11px]"
          disabled={!dirty || saving}
          onClick={save}
        >
          {saving ? "…" : "保存"}
        </Button>
      </td>
    </tr>
  );
}
