module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire en_i,
    input wire regfile_intg_err_i,
    input wire shadowed_update_err_i,
    input wire shadowed_storage_err_i,
    input wire reseed_cnt_err_i,
    input wire sideload_sel_err_i,
    input wire sideload_fsm_err_i,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (en_i) |-> ($stable(regfile_intg_err_i) && $stable(shadowed_update_err_i) && $stable(shadowed_storage_err_i) && $stable(reseed_cnt_err_i) && $stable(sideload_sel_err_i) && $stable(sideload_fsm_err_i)));

endmodule


bind keymgr_ctrl sva_checker sva_checker_inst (.*);
