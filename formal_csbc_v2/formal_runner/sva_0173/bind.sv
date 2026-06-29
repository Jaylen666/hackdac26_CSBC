module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire valid,
    input wire inputs_invalid_i,
    input wire adv_en_i,
    input wire id_en_i,
    input wire gen_en_i,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (valid && $past(valid)) |-> (inputs_invalid_i == $past(inputs_invalid_i)) && (adv_en_i == $past(adv_en_i)) && (id_en_i == $past(id_en_i)) && (gen_en_i == $past(gen_en_i)));

endmodule


bind keymgr_kmac_if sva_checker sva_checker_inst (.*);
